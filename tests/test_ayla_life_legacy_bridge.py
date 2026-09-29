from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ayla_life.legacy_bridge import (
    GenesisDryRunner,
    LegacyEconomyReader,
    LegacyGenesisExecutor,
    LegacyGenesisPlanner,
    LegacySchemaError,
    ValidationSeverity,
)


def make_legacy(rows, *, order=None, missing=False):
    handle = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    handle.close()
    path = Path(handle.name)
    with closing(sqlite3.connect(path)) as db:
        if missing:
            db.execute("CREATE TABLE economy_profiles (user_id INTEGER)")
        else:
            db.execute("CREATE TABLE economy_profiles (user_id INTEGER, balance INTEGER, daily_streak INTEGER, last_daily_at INTEGER, updated_at INTEGER)")
            values = list(rows)
            if order:
                values = [values[index] for index in order]
            db.executemany("INSERT INTO economy_profiles VALUES (?,?,?,?,?)", values)
        db.commit()
    return path


class LegacyBridgeTests(unittest.TestCase):
    def tearDown(self):
        for path in getattr(self, "paths", []):
            Path(path).unlink(missing_ok=True)

    def register(self, path):
        self.paths = getattr(self, "paths", []) + [path]
        return path

    def test_reader_is_read_only_and_preserves_all_legacy_fields(self):
        path = self.register(make_legacy([(7, 100, 4, 123, 456)]))
        snapshot = LegacyEconomyReader(path).read_snapshot()
        self.assertEqual(snapshot.profiles[0].daily_streak, 4)
        with self.assertRaises(sqlite3.OperationalError):
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
                db.execute("UPDATE economy_profiles SET balance=0")

    def test_missing_schema_is_blocked(self):
        path = self.register(make_legacy([], missing=True))
        with self.assertRaises(LegacySchemaError):
            LegacyEconomyReader(path).read_snapshot()

    def test_validation_detects_invalid_rows_duplicates_and_outlier(self):
        rows = [
            (1, 100, 0, None, 1),
            (2, 0, 2, None, 1),
            (3, -1, 0, None, 1),
            (4, "bad", 0, None, 1),
            (1, 200, 0, None, 1),
            (5, 2_000_000, 0, None, 1),
        ]
        path = self.register(make_legacy(rows))
        report = LegacyEconomyReader(path).validate(LegacyEconomyReader(path).read_snapshot())
        codes = {issue.code for issue in report.issues}
        self.assertTrue({"NEGATIVE_BALANCE", "INVALID_BALANCE", "DUPLICATE_USER_ID"} <= codes)
        self.assertIn("BALANCE_OUTLIER", codes)
        self.assertEqual([p.user_id for p in report.valid_profiles], [2, 5])
        self.assertEqual(report.maximum, 2_000_000)
        self.assertGreaterEqual(report.p95, 2_000_000)

    def test_hash_is_order_independent_and_changes_with_row_content(self):
        rows = [(1, 10, 0, None, 1), (2, 20, 1, 2, 3)]
        first = self.register(make_legacy(rows, order=[0, 1]))
        second = self.register(make_legacy(rows, order=[1, 0]))
        changed = self.register(make_legacy([(1, 11, 0, None, 1), rows[1]]))
        a, b, c = [LegacyEconomyReader(p).read_snapshot() for p in (first, second, changed)]
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertEqual(a.snapshot_id, b.snapshot_id)
        self.assertNotEqual(a.content_hash, c.content_hash)

    def test_plan_is_deterministic_and_zero_balances_get_accounts(self):
        path = self.register(make_legacy([(42, 100, 9, 10, 11), (43, 0, 0, None, 11)]))
        reader = LegacyEconomyReader(path)
        snapshot = reader.read_snapshot()
        validation = reader.validate(snapshot)
        plan = LegacyGenesisPlanner().plan(snapshot, validation)
        self.assertEqual(len(plan.accounts), 2)
        self.assertEqual(len(plan.transactions), 1)
        self.assertEqual(plan.accounts[0].account_id, "player:discord:42")
        self.assertEqual(plan.transactions[0].idempotency_key, f"legacy-genesis:{snapshot.snapshot_id}:42:WINK")
        self.assertEqual(plan.transactions[0].metadata["daily_streak"], 9)
        self.assertEqual(plan.manifest.status, "VALIDATED")
        self.assertEqual(json.loads(plan.to_json())["manifest"]["content_hash"], snapshot.content_hash)

    def test_dry_run_verifies_one_to_one_totals_reconciliation_and_replay(self):
        path = self.register(make_legacy([(1, 100, 1, None, 1), (2, 0, 0, None, 1), (3, 250, 2, 3, 4)]))
        reader = LegacyEconomyReader(path)
        snapshot = reader.read_snapshot()
        plan = LegacyGenesisPlanner().plan(snapshot, reader.validate(snapshot))
        report = GenesisDryRunner().run(plan)
        self.assertEqual(report.status, "READY_FOR_CUTOVER")
        self.assertEqual(report.legacy_total, 350)
        self.assertEqual(report.genesis_total, 350)
        self.assertEqual(report.player_total, 350)
        self.assertFalse(report.mismatches)
        self.assertTrue(report.reconciliation_ok)
        self.assertTrue(report.idempotency_replay_ok)

    def test_blocked_snapshot_cannot_dry_run_and_changed_snapshot_has_new_keys(self):
        first = self.register(make_legacy([(1, 10, 0, None, 1)]))
        second = self.register(make_legacy([(1, 11, 0, None, 1)]))
        r1, r2 = LegacyEconomyReader(first), LegacyEconomyReader(second)
        s1, s2 = r1.read_snapshot(), r2.read_snapshot()
        p1 = LegacyGenesisPlanner().plan(s1, r1.validate(s1))
        p2 = LegacyGenesisPlanner().plan(s2, r2.validate(s2))
        self.assertNotEqual(p1.snapshot_id, p2.snapshot_id)
        self.assertNotEqual(p1.transactions[0].idempotency_key, p2.transactions[0].idempotency_key)
        blocked = self.register(make_legacy([(1, -10, 0, None, 1)]))
        rb = LegacyEconomyReader(blocked)
        sb = rb.read_snapshot()
        pb = LegacyGenesisPlanner().plan(sb, rb.validate(sb))
        with self.assertRaises(ValueError):
            GenesisDryRunner().run(pb)

    def test_real_executor_is_explicitly_disabled(self):
        with self.assertRaises(RuntimeError):
            LegacyGenesisExecutor().apply()


if __name__ == "__main__":
    unittest.main()
