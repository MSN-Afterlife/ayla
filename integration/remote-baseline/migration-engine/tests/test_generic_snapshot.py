from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lib.generic.domain import MigrationDataFinding, MigrationIdentity, MigrationInspection, MigrationPlan
from lib.generic.planner import GenericPlanner
from lib.generic.snapshot import (
    DatabaseSnapshotEntry,
    FileSnapshotEntry,
    GranularSnapshotManager,
    SnapshotError,
    SnapshotManifest,
)


class GranularSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "server"
        self.runs = Path(self.tmp.name) / "runs"
        self.root.mkdir()
        self.runs.mkdir()

        self.leg_u = "11111111-1111-1111-1111-111111111111"
        self.can_u = "22222222-2222-2222-2222-222222222222"
        self.other_u = "33333333-3333-3333-3333-333333333333"

        self.identity = MigrationIdentity(
            discord_user_id="1001",
            canonical_name="PlayerOne",
            canonical_uuid=self.can_u,
            legacy_name="OldOne",
            legacy_uuid=self.leg_u,
        )

        # Setup sample files
        self.pdata_dir = self.root / "world/players/data"
        self.pdata_dir.mkdir(parents=True)
        self.src_dat = self.pdata_dir / f"{self.leg_u}.dat"
        self.src_dat.write_bytes(b"DATA_PLAYER_ONE")

        # Setup sample SQLite DB with PlayerOne and PlayerOther
        self.db_dir = self.root / "plugins/HuskHomes"
        self.db_dir.mkdir(parents=True)
        self.husk_db = self.db_dir / "HuskHomesData.db"
        with sqlite3.connect(str(self.husk_db)) as conn:
            conn.execute(
                "CREATE TABLE huskhomes_saved_positions (id INTEGER PRIMARY KEY, player_uuid TEXT, name TEXT)"
            )
            conn.execute(
                "INSERT INTO huskhomes_saved_positions (id, player_uuid, name) VALUES (1, ?, 'home1')",
                (self.leg_u,),
            )
            conn.execute(
                "INSERT INTO huskhomes_saved_positions (id, player_uuid, name) VALUES (2, ?, 'home2')",
                (self.other_u,),
            )

        self.mgr = GranularSnapshotManager(self.runs)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_plan(self, target_existed: bool = False) -> MigrationPlan:
        changes = [
            {
                "adapter": "vanilla_playerdata",
                "action": "MOVE_FILE",
                "source": f"world/players/data/{self.leg_u}.dat",
                "target": f"world/players/data/{self.can_u}.dat",
                "records": 1,
                "strategy": "MOVE_TO_TARGET",
            },
            {
                "adapter": "huskhomes",
                "action": "UPDATE_SQL_ROWS",
                "database": "plugins/HuskHomes/HuskHomesData.db",
                "records": 1,
                "strategy": "SQL_UPDATE_INVERSE_DML",
            },
        ]
        return MigrationPlan(
            plan_id="plan-test-01",
            created_at="2026-09-05T00:00:00Z",
            identity=self.identity,
            inspection_fingerprint="fp123456",
            status="READY",
            planned_changes=changes,
        )

    def test_01_snapshot_source_file(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-01", plan, self.root)

        entry = next(e for e in manifest.file_entries if e.adapter == "vanilla_playerdata")
        self.assertTrue(entry.source_existed)
        self.assertIsNotNone(entry.backup_source_rel)
        backup_file = self.runs / "mig-01" / entry.backup_source_rel
        self.assertTrue(backup_file.is_file())
        self.assertEqual(backup_file.read_bytes(), b"DATA_PLAYER_ONE")

    def test_02_snapshot_target_absent(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-02", plan, self.root)
        entry = next(e for e in manifest.file_entries if e.adapter == "vanilla_playerdata")
        self.assertFalse(entry.target_existed)
        self.assertIsNone(entry.backup_target_rel)

    def test_03_snapshot_target_existing(self):
        tgt_file = self.pdata_dir / f"{self.can_u}.dat"
        tgt_file.write_bytes(b"PRE_EXISTING_TARGET")

        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-03", plan, self.root)
        entry = next(e for e in manifest.file_entries if e.adapter == "vanilla_playerdata")
        self.assertTrue(entry.target_existed)
        self.assertIsNotNone(entry.backup_target_rel)
        backup_tgt = self.runs / "mig-03" / entry.backup_target_rel
        self.assertEqual(backup_tgt.read_bytes(), b"PRE_EXISTING_TARGET")

    def test_04_sha256_before_correct(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-04", plan, self.root)
        entry = next(e for e in manifest.file_entries if e.adapter == "vanilla_playerdata")
        expected_hash = GranularSnapshotManager.sha256_file(self.src_dat)
        self.assertEqual(entry.source_sha256_before, expected_hash)

    def test_05_ownership_saved(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-05", plan, self.root)
        entry = next(e for e in manifest.file_entries if e.adapter == "vanilla_playerdata")
        stat = self.src_dat.stat()
        self.assertEqual(entry.uid, stat.st_uid)
        self.assertEqual(entry.gid, stat.st_gid)

    def test_06_mode_saved(self):
        self.src_dat.chmod(0o640)
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-06", plan, self.root)
        entry = next(e for e in manifest.file_entries if e.adapter == "vanilla_playerdata")
        self.assertEqual(entry.mode, self.src_dat.stat().st_mode)

    def test_07_manifest_atomic(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-07", plan, self.root)
        manifest_file = self.runs / "mig-07" / "manifest.json"
        tmp_file = self.runs / "mig-07" / "manifest.json.tmp"
        self.assertTrue(manifest_file.is_file())
        self.assertFalse(tmp_file.exists())
        loaded = json.loads(manifest_file.read_text())
        self.assertEqual(loaded["migration_id"], "mig-07")

    def test_08_snapshot_sqlite_single_row(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-08", plan, self.root)
        db_entry = next(e for e in manifest.database_entries if e.adapter == "huskhomes")
        self.assertEqual(len(db_entry.rows), 1)
        row = db_entry.rows[0]
        self.assertEqual(row["pk_value"], 1)
        self.assertEqual(row["before"]["player_uuid"], self.leg_u)
        self.assertEqual(row["planned_after"]["player_uuid"], self.can_u)

    def test_09_snapshot_sqlite_multiple_rows(self):
        with sqlite3.connect(str(self.husk_db)) as conn:
            conn.execute(
                "INSERT INTO huskhomes_saved_positions (id, player_uuid, name) VALUES (3, ?, 'home3')",
                (self.leg_u,),
            )
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-09", plan, self.root)
        db_entry = next(e for e in manifest.database_entries if e.adapter == "huskhomes")
        self.assertEqual(len(db_entry.rows), 2)
        pks = {r["pk_value"] for r in db_entry.rows}
        self.assertEqual(pks, {1, 3})

    def test_10_no_other_player_data_in_snapshot(self):
        plan = self._make_plan()
        manifest = self.mgr.create_snapshot("mig-10", plan, self.root)
        db_entry = next(e for e in manifest.database_entries if e.adapter == "huskhomes")
        for r in db_entry.rows:
            self.assertNotEqual(r["pk_value"], 2)
            self.assertNotEqual(r["before"]["player_uuid"], self.other_u)

    def test_11_arbitrary_path_rejected(self):
        evil_plan = MigrationPlan(
            plan_id="plan-evil",
            created_at="2026-09-05T00:00:00Z",
            identity=self.identity,
            inspection_fingerprint="fp_evil",
            status="READY",
            planned_changes=[
                {
                    "adapter": "vanilla_playerdata",
                    "action": "MOVE_FILE",
                    "source": "../../../etc/passwd",
                    "target": "world/players/data/out.dat",
                    "records": 1,
                    "strategy": "MOVE_TO_TARGET",
                }
            ],
        )
        with self.assertRaises(SnapshotError):
            self.mgr.create_snapshot("mig-11", evil_plan, self.root)

    def test_12_snapshot_aborts_if_precondition_incomplete(self):
        bad_identity = MigrationIdentity(
            discord_user_id="1001",
            canonical_name="Bad",
            canonical_uuid=self.can_u,
            legacy_uuid=None,  # Missing!
        )
        bad_plan = MigrationPlan(
            plan_id="plan-bad",
            created_at="2026-09-05T00:00:00Z",
            identity=bad_identity,
            inspection_fingerprint="fp_bad",
            status="INCOMPLETE",
        )
        with self.assertRaises(SnapshotError):
            self.mgr.create_snapshot("mig-12", bad_plan, self.root)
