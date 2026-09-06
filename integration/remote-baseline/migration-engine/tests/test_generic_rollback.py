from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lib.generic.domain import MigrationIdentity, MigrationPlan
from lib.generic.rollback import GranularRollbackManager, RollbackConflictError, RollbackError
from lib.generic.snapshot import GranularSnapshotManager


class GranularRollbackTests(unittest.TestCase):
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

        # Setup files
        self.pdata_dir = self.root / "world/players/data"
        self.pdata_dir.mkdir(parents=True)
        self.src_dat = self.pdata_dir / f"{self.leg_u}.dat"
        self.tgt_dat = self.pdata_dir / f"{self.can_u}.dat"
        self.src_dat.write_bytes(b"ORIGINAL_PLAYER_DATA")

        # Setup SQLite
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
                "INSERT INTO huskhomes_saved_positions (id, player_uuid, name) VALUES (2, ?, 'home_other')",
                (self.other_u,),
            )

        self.snap_mgr = GranularSnapshotManager(self.runs)
        self.rb_mgr = GranularRollbackManager(self.runs)

    def tearDown(self):
        self.tmp.cleanup()

    def _prepare_and_migrate(self, target_existed_before: bool = False):
        if target_existed_before:
            self.tgt_dat.write_bytes(b"ORIGINAL_TARGET_DATA")

        plan = MigrationPlan(
            plan_id="plan-rb-01",
            created_at="2026-09-05T00:00:00Z",
            identity=self.identity,
            inspection_fingerprint="fp_rb",
            status="READY",
            planned_changes=[
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
            ],
        )
        manifest = self.snap_mgr.create_snapshot("mig-rb-01", plan, self.root)

        # Simulate mutation (apply)
        self.src_dat.unlink()
        self.tgt_dat.write_bytes(b"MIGRATED_PLAYER_DATA")
        with sqlite3.connect(str(self.husk_db)) as conn:
            conn.execute(
                "UPDATE huskhomes_saved_positions SET player_uuid = ? WHERE id = 1",
                (self.can_u,),
            )

        return manifest

    def test_13_rollback_source_file_restored(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)
        self.assertFalse(self.src_dat.exists())

        res = self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.assertEqual(res["status"], "ROLLED_BACK")
        self.assertTrue(self.src_dat.is_file())
        self.assertEqual(self.src_dat.read_bytes(), b"ORIGINAL_PLAYER_DATA")

    def test_14_rollback_removes_created_target(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)
        self.assertTrue(self.tgt_dat.exists())

        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.assertFalse(self.tgt_dat.exists())

    def test_15_rollback_restores_pre_existing_target(self):
        manifest = self._prepare_and_migrate(target_existed_before=True)
        self.assertEqual(self.tgt_dat.read_bytes(), b"MIGRATED_PLAYER_DATA")

        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.assertTrue(self.tgt_dat.is_file())
        self.assertEqual(self.tgt_dat.read_bytes(), b"ORIGINAL_TARGET_DATA")

    def test_16_rollback_maintains_mode(self):
        self.src_dat.chmod(0o600)
        manifest = self._prepare_and_migrate(target_existed_before=False)
        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.assertEqual(self.src_dat.stat().st_mode & 0o777, 0o600)

    def test_17_inverse_dml_restores_only_affected_pk(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)
        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)

        with sqlite3.connect(str(self.husk_db)) as conn:
            row1 = conn.execute("SELECT player_uuid FROM huskhomes_saved_positions WHERE id = 1").fetchone()
            self.assertEqual(row1[0], self.leg_u)

    def test_18_player_b_remains_intact(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)
        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)

        with sqlite3.connect(str(self.husk_db)) as conn:
            row2 = conn.execute("SELECT player_uuid, name FROM huskhomes_saved_positions WHERE id = 2").fetchone()
            self.assertEqual(row2[0], self.other_u)
            self.assertEqual(row2[1], "home_other")

    def test_19_row_modified_after_migration_raises_rollback_conflict(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)

        # External change happens after migration: row 1 changed to someone else
        with sqlite3.connect(str(self.husk_db)) as conn:
            conn.execute("UPDATE huskhomes_saved_positions SET player_uuid = 'evil-changed-uuid' WHERE id = 1")

        with self.assertRaises(RollbackConflictError) as ctx:
            self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)

        self.assertIn("ROLLBACK_CONFLICT", str(ctx.exception))

    def test_20_rollback_is_idempotent(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)
        res1 = self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.assertEqual(res1["status"], "ROLLED_BACK")

        # Second rollback immediately following first
        res2 = self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.assertEqual(res2["status"], "ROLLED_BACK")
        self.assertEqual(self.src_dat.read_bytes(), b"ORIGINAL_PLAYER_DATA")

    def test_21_second_rollback_does_not_duplicate_or_corrupt_data(self):
        manifest = self._prepare_and_migrate(target_existed_before=False)
        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)
        self.rb_mgr.rollback("mig-rb-01", self.root, manifest=manifest)

        with sqlite3.connect(str(self.husk_db)) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM huskhomes_saved_positions").fetchone()[0]
            self.assertEqual(cnt, 2)

    def test_22_missing_manifest_raises_rollback_error(self):
        with self.assertRaises(RollbackError):
            self.rb_mgr.rollback("non-existent-migration-id", self.root)
