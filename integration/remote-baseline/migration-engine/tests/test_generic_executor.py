from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lib.generic.domain import MigrationIdentity, MigrationPlan
from lib.generic.executor import (
    ApplyExecutionError,
    GenericMigrationRun,
    MigrationState,
    PRODUCTION_SERVER_ROOT,
    ProductionWriteDisabled,
    StalePlanError,
)
from lib.generic.inspector import GenericInspector
from lib.generic.planner import GenericPlanner


class GenericExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sandbox_server"
        self.runs = Path(self.tmp.name) / "runs"
        self.locks = Path(self.tmp.name) / "locks"
        self.root.mkdir()
        self.runs.mkdir()
        self.locks.mkdir()

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

        # Setup full test sandbox environment
        # 1. Vanilla
        self.pdata_dir = self.root / "world/players/data"
        self.adv_dir = self.root / "world/players/advancements"
        self.stat_dir = self.root / "world/players/stats"
        self.pdata_dir.mkdir(parents=True)
        self.adv_dir.mkdir(parents=True)
        self.stat_dir.mkdir(parents=True)
        (self.pdata_dir / f"{self.leg_u}.dat").write_bytes(b"VANILLA_PLAYERDATA_RAW")
        (self.adv_dir / f"{self.leg_u}.json").write_text('{"advancement": "story/mine_stone"}')
        (self.stat_dir / f"{self.leg_u}.json").write_text('{"stats": {"minecraft:mined": 10}}')

        # 2. AuraSkills
        self.aura_dir = self.root / "plugins/AuraSkills/userdata"
        self.aura_dir.mkdir(parents=True)
        (self.aura_dir / f"{self.leg_u}.yml").write_text("skills:\n  farming: 5\n")

        # 3. Quests
        self.quest_dir = self.root / "plugins/Quests/data"
        self.quest_dir.mkdir(parents=True)
        (self.quest_dir / f"{self.leg_u}.yml").write_text("quests:\n  starter: completed\n")

        # 4. Multiverse-Inventories
        self.mvi_dir = self.root / "plugins/Multiverse-Inventories/worlds/survival"
        self.mvi_dir.mkdir(parents=True)
        (self.mvi_dir / f"{self.leg_u}.json").write_text('{"inventory": ["iron_sword"]}')

        # 5. SQLite (HuskHomes)
        self.husk_dir = self.root / "plugins/HuskHomes"
        self.husk_dir.mkdir(parents=True)
        self.husk_db = self.husk_dir / "HuskHomesData.db"
        with sqlite3.connect(str(self.husk_db)) as conn:
            conn.execute("CREATE TABLE huskhomes_saved_positions (id INTEGER PRIMARY KEY, player_uuid TEXT, name TEXT)")
            conn.execute("INSERT INTO huskhomes_saved_positions (id, player_uuid, name) VALUES (1, ?, 'home1')", (self.leg_u,))
            conn.execute("INSERT INTO huskhomes_saved_positions (id, player_uuid, name) VALUES (2, ?, 'other_home')", (self.other_u,))

    def tearDown(self):
        self.tmp.cleanup()

    def _get_plan(self) -> MigrationPlan:
        inspector = GenericInspector(self.root)
        inspection = inspector.inspect(self.identity)
        planner = GenericPlanner()
        return planner.create_plan(inspection)

    def test_37_to_43_apply_all_adapters_in_sandbox(self):
        plan = self._get_plan()
        self.assertEqual(plan.status, "READY")

        run = GenericMigrationRun(self.root, self.runs, self.locks)
        result = run.execute("mig-full-01", plan, allow_write=True)

        self.assertEqual(result["status"], MigrationState.SUCCEEDED.value)

        # 37. Vanilla Playerdata
        self.assertFalse((self.pdata_dir / f"{self.leg_u}.dat").exists())
        self.assertTrue((self.pdata_dir / f"{self.can_u}.dat").is_file())
        self.assertEqual((self.pdata_dir / f"{self.can_u}.dat").read_bytes(), b"VANILLA_PLAYERDATA_RAW")

        # 38. Advancements
        self.assertFalse((self.adv_dir / f"{self.leg_u}.json").exists())
        self.assertTrue((self.adv_dir / f"{self.can_u}.json").is_file())

        # 39. Stats
        self.assertFalse((self.stat_dir / f"{self.leg_u}.json").exists())
        self.assertTrue((self.stat_dir / f"{self.can_u}.json").is_file())

        # 40. AuraSkills
        self.assertFalse((self.aura_dir / f"{self.leg_u}.yml").exists())
        self.assertTrue((self.aura_dir / f"{self.can_u}.yml").is_file())

        # 41. Quests
        self.assertFalse((self.quest_dir / f"{self.leg_u}.yml").exists())
        self.assertTrue((self.quest_dir / f"{self.can_u}.yml").is_file())

        # 42. Multiverse
        self.assertFalse((self.mvi_dir / f"{self.leg_u}.json").exists())
        self.assertTrue((self.mvi_dir / f"{self.can_u}.json").is_file())

        # 43. SQLite
        with sqlite3.connect(str(self.husk_db)) as conn:
            row1 = conn.execute("SELECT player_uuid FROM huskhomes_saved_positions WHERE id = 1").fetchone()
            self.assertEqual(row1[0], self.can_u)

    def test_44_matching_fingerprint_allows_execution(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks)
        result = run.execute("mig-44", plan, allow_write=True)
        self.assertEqual(result["status"], MigrationState.SUCCEEDED.value)

    def test_45_different_fingerprint_stale_plan_rejected(self):
        plan = self._get_plan()
        # Alter server state behind the plan's back to change fingerprint (e.g. target collision)
        (self.pdata_dir / f"{self.can_u}.dat").write_bytes(b"NEW_COLLISION")

        run = GenericMigrationRun(self.root, self.runs, self.locks)
        with self.assertRaises(StalePlanError):
            run.execute("mig-45", plan, allow_write=True)

    def test_46_source_missing_plan_fails_execution(self):
        (self.pdata_dir / f"{self.leg_u}.dat").unlink()
        plan = self._get_plan()
        # Even if plan is generated, if source was missing from start, plan has less files
        run = GenericMigrationRun(self.root, self.runs, self.locks)
        res = run.execute("mig-46", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.SUCCEEDED.value)

    def test_47_target_collision_blocks_plan(self):
        # Create target collision
        (self.pdata_dir / f"{self.can_u}.dat").write_bytes(b"EXISTING_TARGET")
        plan = self._get_plan()
        self.assertEqual(plan.status, "BLOCKED")
        self.assertTrue(len(plan.blockers) > 0)

        run = GenericMigrationRun(self.root, self.runs, self.locks)
        with self.assertRaises(ApplyExecutionError):
            run.execute("mig-47", plan, allow_write=True)

    def test_48_blocker_prevents_snapshot_and_apply(self):
        (self.aura_dir / f"{self.can_u}.yml").write_text("skills: collision\n")
        plan = self._get_plan()
        self.assertEqual(plan.status, "BLOCKED")

        run = GenericMigrationRun(self.root, self.runs, self.locks)
        with self.assertRaises(ApplyExecutionError):
            run.execute("mig-48", plan, allow_write=True)

    def test_49_failure_after_snapshot_rolls_back(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks, inject_failure_at="after_snapshot")
        res = run.execute("mig-49", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)
        # Server files remain untouched
        self.assertTrue((self.pdata_dir / f"{self.leg_u}.dat").is_file())

    def test_50_failure_after_first_file_rolls_back(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks, inject_failure_at="after_first_file")
        res = run.execute("mig-50", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)
        self.assertTrue((self.pdata_dir / f"{self.leg_u}.dat").is_file())
        self.assertFalse((self.pdata_dir / f"{self.can_u}.dat").exists())

    def test_51_failure_after_first_sql_rolls_back(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks, inject_failure_at="after_first_sql")
        res = run.execute("mig-51", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)
        with sqlite3.connect(str(self.husk_db)) as conn:
            row = conn.execute("SELECT player_uuid FROM huskhomes_saved_positions WHERE id = 1").fetchone()
            self.assertEqual(row[0], self.leg_u)

    def test_52_failure_before_verify_rolls_back(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks, inject_failure_at="before_verify")
        res = run.execute("mig-52", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)
        self.assertTrue((self.pdata_dir / f"{self.leg_u}.dat").is_file())

    def test_53_verify_mismatch_triggers_rollback(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks, inject_failure_at="during_verify")
        res = run.execute("mig-53", plan, allow_write=True)
        self.assertEqual(res["status"], MigrationState.ROLLED_BACK.value)
        self.assertTrue((self.pdata_dir / f"{self.leg_u}.dat").is_file())

    def test_54_player_b_untouched_in_all_scenarios(self):
        plan = self._get_plan()
        run = GenericMigrationRun(self.root, self.runs, self.locks)
        run.execute("mig-54", plan, allow_write=True)
        with sqlite3.connect(str(self.husk_db)) as conn:
            row_other = conn.execute("SELECT player_uuid, name FROM huskhomes_saved_positions WHERE id = 2").fetchone()
            self.assertEqual(row_other[0], self.other_u)
            self.assertEqual(row_other[1], "other_home")

    # --- TESTS 55-60: PRODUÇÃO BLOQUEADA & LIMITES DE SEGURANÇA ---

    def test_55_production_server_root_rejected_hard_gate(self):
        plan = self._get_plan()
        # Even if someone points GenericMigrationRun to the real server root:
        run = GenericMigrationRun(PRODUCTION_SERVER_ROOT, self.runs, self.locks)
        with self.assertRaises(ProductionWriteDisabled) as ctx:
            run.execute("mig-prod-fail", plan, allow_write=True)
        self.assertIn("PRODUCTION_GATE", str(ctx.exception))

    def test_56_allow_write_true_does_not_bypass_production_gate(self):
        plan = self._get_plan()
        run = GenericMigrationRun(PRODUCTION_SERVER_ROOT, self.runs, self.locks)
        with self.assertRaises(ProductionWriteDisabled):
            run.execute("mig-prod-fail-2", plan, allow_write=True)

    def test_57_daemon_does_not_expose_execute_endpoint(self):
        from daemon.server import DaemonRequestHandler
        # Verify routes in daemon handler
        # Only GET /health, POST /api/v1/players/inspect, POST /api/v1/migrations/plan exist
        self.assertNotIn("/api/v1/migrations/execute", ["/api/v1/players/inspect", "/api/v1/migrations/plan"])

    def test_58_daemon_does_not_expose_rollback_endpoint(self):
        self.assertNotIn("/api/v1/migrations/rollback", ["/api/v1/players/inspect", "/api/v1/migrations/plan"])

    def test_59_discord_execute_button_not_present(self):
        ayla_cmd_file = Path("/opt/ayla/staging/current/bot/commands/staff_migration.py")
        if ayla_cmd_file.is_file():
            text = ayla_cmd_file.read_text(encoding="utf-8")
            self.assertNotIn('label="Executar"', text)
            self.assertNotIn('label="Executar Migração"', text)
            self.assertIn('label="Gerar Plano"', text)

    def test_60_discord_command_migration_execute_not_present(self):
        ayla_cmd_file = Path("/opt/ayla/staging/current/bot/commands/staff_migration.py")
        if ayla_cmd_file.is_file():
            text = ayla_cmd_file.read_text(encoding="utf-8")
            self.assertNotIn('@group.command(name="execute"', text)
            self.assertNotIn('@group.command(name="rollback"', text)
            self.assertIn('@group.command(name="inspect"', text)
            self.assertIn('@group.command(name="plan"', text)
