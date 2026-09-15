from __future__ import annotations

import json
import pathlib
import unittest

from ayla_life.legacy_bridge import GenesisDryRunner, LegacyEconomyReader, LegacyGenesisPlanner


ARTIFACT_ROOT = pathlib.Path(r"C:\Users\Abroba\ayla-life-rehearsal")
SNAPSHOT = ARTIFACT_ROOT / "levels.snapshot.sqlite3"
MANIFEST = ARTIFACT_ROOT / "snapshot_manifest.json"
DATASET = ARTIFACT_ROOT / "m1_legacy_dataset.json"
HAS_REAL_ARTIFACTS = all(path.exists() for path in (SNAPSHOT, MANIFEST, DATASET))


@unittest.skipUnless(HAS_REAL_ARTIFACTS, "real rehearsal artifacts are not present")
class RealM1A3RehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = LegacyEconomyReader(SNAPSHOT)
        cls.snapshot = cls.reader.read_snapshot()
        cls.validation = cls.reader.validate(cls.snapshot)
        cls.plan = LegacyGenesisPlanner().plan(cls.snapshot, cls.validation)

    def test_canonical_identity_and_manifest_provenance(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(self.snapshot.content_hash, "7023d48e4902f8a17b00737d2c1db4b959757b8c60971835a45e8314eda1ddba")
        self.assertEqual(self.snapshot.schema_fingerprint, "130171392a04e81e25af41b5893c61ad3e018df8ff90e70ac6eb32e775af161b")
        self.assertEqual(manifest["row_count"], 9)
        self.assertEqual(manifest["total_balance"], 108340)

    def test_real_dataset_and_plan_shape(self):
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        self.assertEqual(len(dataset["accounts"]), 9)
        self.assertEqual(dataset["totals"]["legacy_wink_supply"], 108340)
        self.assertEqual(self.validation.blocked_count, 0)
        self.assertEqual(len(self.plan.accounts), 9)
        self.assertEqual(len(self.plan.transactions), 7)

    def test_real_genesis_dry_run(self):
        report = GenesisDryRunner().run(self.plan)
        self.assertEqual(report.status, "READY_FOR_CUTOVER")
        self.assertEqual(report.legacy_total, 108340)
        self.assertEqual(report.genesis_total, 108340)
        self.assertEqual(report.player_total, 108340)
        self.assertEqual(report.mismatches, ())
        self.assertTrue(report.reconciliation_ok)
        self.assertTrue(report.idempotency_replay_ok)


if __name__ == "__main__":
    unittest.main()
