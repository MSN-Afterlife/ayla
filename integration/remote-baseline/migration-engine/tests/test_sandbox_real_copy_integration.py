from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lib.generic.domain import MigrationIdentity
from lib.generic.executor import GenericMigrationRun, MigrationState
from lib.generic.inspector import GenericInspector
from lib.generic.planner import GenericPlanner
from lib.generic.rollback import GranularRollbackManager

REAL_SERVER_ROOT = Path("/opt/minecraft/crafty/servers/c253fa7e-2bd2-4545-a9ba-b1a8db892197")
MOUNK_LEGACY_UUID = "555dd93f-a696-3e49-978b-398ad2208571"
CLEAN_TEST_CANONICAL_UUID = "88888888-8888-8888-8888-888888888888"


class SandboxRealCopyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.tmp.name) / "sandbox"
        self.runs = Path(self.tmp.name) / "runs"
        self.locks = Path(self.tmp.name) / "locks"
        self.sandbox.mkdir()
        self.runs.mkdir()
        self.locks.mkdir()

        # Copy ONLY Mounk's real artifacts to isolated sandbox
        self.copied_relpaths = [
            f"world/players/data/{MOUNK_LEGACY_UUID}.dat",
            f"world/players/advancements/{MOUNK_LEGACY_UUID}.json",
            f"world/players/stats/{MOUNK_LEGACY_UUID}.json",
            f"plugins/AuraSkills/userdata/{MOUNK_LEGACY_UUID}.yml",
            f"plugins/Quests/data/{MOUNK_LEGACY_UUID}.yml",
            f"plugins/Multiverse-Inventories/players/{MOUNK_LEGACY_UUID}.json",
            f"plugins/ImageFrame/players/{MOUNK_LEGACY_UUID}.json",
            "plugins/HuskHomes/HuskHomesData.db",
            "plugins/EliteMobs/data/player_data.db",
            "plugins/Waypoints/waypoints.db",
        ]

        for rel in self.copied_relpaths:
            src = REAL_SERVER_ROOT / rel
            if src.is_file():
                dst = self.sandbox / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        self.identity = MigrationIdentity(
            discord_user_id="1533219416379232336",
            canonical_name="Mounk",
            canonical_uuid=CLEAN_TEST_CANONICAL_UUID,
            legacy_name="Mounkass",
            legacy_uuid=MOUNK_LEGACY_UUID,
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _hash_file(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
        return h.hexdigest()

    def test_full_lifecycle_on_isolated_real_copy(self):
        # 1. Capture initial baseline of sandbox copy
        initial_hashes = {}
        for rel in self.copied_relpaths:
            p = self.sandbox / rel
            if p.is_file() and not rel.endswith(".db"):
                initial_hashes[rel] = self._hash_file(p)

        # Capture initial SQL row counts and values
        husk_db = self.sandbox / "plugins/HuskHomes/HuskHomesData.db"
        with sqlite3.connect(f"file:{husk_db}?mode=ro", uri=True) as conn:
            initial_husk_rows = conn.execute(
                "SELECT uuid, username FROM huskhomes_users WHERE uuid = ?",
                (MOUNK_LEGACY_UUID,),
            ).fetchall()
            all_husk_count = conn.execute("SELECT COUNT(*) FROM huskhomes_users").fetchone()[0]

        # 2. Inspect Sandbox
        inspector = GenericInspector(self.sandbox)
        inspection = inspector.inspect(self.identity)
        self.assertEqual(inspection.status, "READY")
        self.assertEqual(len(inspection.blockers), 0)

        # 3. Plan
        planner = GenericPlanner()
        plan = planner.create_plan(inspection)
        self.assertEqual(plan.status, "READY")
        self.assertTrue(len(plan.planned_changes) >= 5)

        # 4. Execute Apply in Sandbox
        run = GenericMigrationRun(self.sandbox, self.runs, self.locks)
        result = run.execute("mig-realcopy-01", plan, allow_write=True)
        self.assertEqual(result["status"], MigrationState.SUCCEEDED.value)

        # Verify applied in sandbox
        tgt_dat = self.sandbox / f"world/players/data/{CLEAN_TEST_CANONICAL_UUID}.dat"
        src_dat = self.sandbox / f"world/players/data/{MOUNK_LEGACY_UUID}.dat"
        self.assertTrue(tgt_dat.is_file())
        self.assertFalse(src_dat.exists())

        with sqlite3.connect(f"file:{husk_db}?mode=ro", uri=True) as conn:
            migrated_husk_rows = conn.execute(
                "SELECT uuid, username FROM huskhomes_users WHERE uuid = ?",
                (CLEAN_TEST_CANONICAL_UUID,),
            ).fetchall()
            self.assertEqual(len(migrated_husk_rows), len(initial_husk_rows))
            # Total count of rows in table remains constant
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM huskhomes_users").fetchone()[0], all_husk_count)

        # 5. Rollback
        rb_mgr = GranularRollbackManager(self.runs)
        rb_res = rb_mgr.rollback("mig-realcopy-01", self.sandbox)
        self.assertEqual(rb_res["status"], "ROLLED_BACK")

        # 6. Verify Rollback Integrity against Initial Baseline
        self.assertTrue(src_dat.is_file())
        self.assertFalse(tgt_dat.exists())

        for rel, expected_hash in initial_hashes.items():
            p = self.sandbox / rel
            self.assertTrue(p.is_file(), f"Arquivo não restaurado: {rel}")
            self.assertEqual(self._hash_file(p), expected_hash, f"Hash divergente para {rel}")

        with sqlite3.connect(f"file:{husk_db}?mode=ro", uri=True) as conn:
            restored_husk_rows = conn.execute(
                "SELECT uuid, username FROM huskhomes_users WHERE uuid = ?",
                (MOUNK_LEGACY_UUID,),
            ).fetchall()
            self.assertEqual(restored_husk_rows, initial_husk_rows)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM huskhomes_users").fetchone()[0], all_husk_count)

    def test_failure_injection_point_1_after_snapshot(self):
        inspector = GenericInspector(self.sandbox)
        plan = GenericPlanner().create_plan(inspector.inspect(self.identity))

        run = GenericMigrationRun(self.sandbox, self.runs, self.locks, inject_failure_at="after_snapshot")
        res = run.execute("mig-fi-01", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)

        # Verify source file was untouched
        src_dat = self.sandbox / f"world/players/data/{MOUNK_LEGACY_UUID}.dat"
        self.assertTrue(src_dat.is_file())

    def test_failure_injection_point_2_after_first_file(self):
        inspector = GenericInspector(self.sandbox)
        plan = GenericPlanner().create_plan(inspector.inspect(self.identity))

        run = GenericMigrationRun(self.sandbox, self.runs, self.locks, inject_failure_at="after_first_file")
        res = run.execute("mig-fi-02", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)

        src_dat = self.sandbox / f"world/players/data/{MOUNK_LEGACY_UUID}.dat"
        tgt_dat = self.sandbox / f"world/players/data/{CLEAN_TEST_CANONICAL_UUID}.dat"
        self.assertTrue(src_dat.is_file())
        self.assertFalse(tgt_dat.exists())

    def test_failure_injection_point_3_before_verify(self):
        inspector = GenericInspector(self.sandbox)
        plan = GenericPlanner().create_plan(inspector.inspect(self.identity))

        run = GenericMigrationRun(self.sandbox, self.runs, self.locks, inject_failure_at="before_verify")
        res = run.execute("mig-fi-03", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)

        # Verify both files and SQL were restored cleanly
        src_dat = self.sandbox / f"world/players/data/{MOUNK_LEGACY_UUID}.dat"
        tgt_dat = self.sandbox / f"world/players/data/{CLEAN_TEST_CANONICAL_UUID}.dat"
        self.assertTrue(src_dat.is_file())
        self.assertFalse(tgt_dat.exists())

        husk_db = self.sandbox / "plugins/HuskHomes/HuskHomesData.db"
        with sqlite3.connect(f"file:{husk_db}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT uuid FROM huskhomes_users WHERE uuid = ?",
                (MOUNK_LEGACY_UUID,),
            ).fetchall()
            self.assertTrue(len(rows) > 0)
