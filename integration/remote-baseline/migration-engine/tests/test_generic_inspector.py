import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lib.generic.domain import MigrationIdentity
from lib.generic.inspector import GenericInspector
from lib.generic.planner import GenericPlanner


class TestGenericInspector(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Setup standard Minecraft folder hierarchy
        (self.root / "world/playerdata").mkdir(parents=True)
        (self.root / "world/advancements").mkdir(parents=True)
        (self.root / "world/stats").mkdir(parents=True)
        (self.root / "plugins/AuraSkills/userdata").mkdir(parents=True)
        (self.root / "plugins/Quests/data").mkdir(parents=True)
        (self.root / "plugins/Multiverse-Inventories/worlds/world").mkdir(parents=True)
        (self.root / "plugins/HuskHomes").mkdir(parents=True)
        (self.root / "plugins/LuckPerms").mkdir(parents=True)

        self.legacy_uuid = "11111111-2222-3333-4444-555555555555"
        self.canonical_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.canonical_name = "PlayerOne"
        self.legacy_name = "OldPlayer"

        self.identity = MigrationIdentity(
            discord_user_id="999",
            canonical_name=self.canonical_name,
            canonical_uuid=self.canonical_uuid,
            legacy_name=self.legacy_name,
            legacy_uuid=self.legacy_uuid,
        )

        # Populate synthetic source files
        (self.root / f"world/playerdata/{self.legacy_uuid}.dat").write_bytes(b"\x1f\x8b\x08nbtdata")
        (self.root / f"world/advancements/{self.legacy_uuid}.json").write_text('{"adv": true}')
        (self.root / f"world/stats/{self.legacy_uuid}.json").write_text('{"stats": 10}')
        (self.root / f"plugins/AuraSkills/userdata/{self.legacy_uuid}.yml").write_text("skills: 5")
        (self.root / f"plugins/Quests/data/{self.legacy_uuid}.yml").write_text("quests: [1]")
        (self.root / f"plugins/Multiverse-Inventories/worlds/world/{self.legacy_name}.json").write_text('{"inv": []}')

        # Populate synthetic SQLite database for HuskHomes
        db_path = self.root / "plugins/HuskHomes/HuskHomesData.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE huskhomes_saved_positions (id INTEGER PRIMARY KEY, player_uuid TEXT)")
            conn.execute("INSERT INTO huskhomes_saved_positions (player_uuid) VALUES (?)", (self.legacy_uuid,))
            conn.execute("INSERT INTO huskhomes_saved_positions (player_uuid) VALUES (?)", (self.legacy_uuid,))
            conn.execute("CREATE TABLE huskhomes_users (id INTEGER PRIMARY KEY, uuid TEXT)")
            conn.execute("INSERT INTO huskhomes_users (uuid) VALUES (?)", (self.legacy_uuid,))

        # Synthetic LuckPerms file
        (self.root / "plugins/LuckPerms/luckperms-h2-v2.mv.db").write_bytes(b"H2DATA")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_04_source_playerdata_found(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        pd_finding = next(f for f in insp.findings if f.adapter == "vanilla_playerdata")
        self.assertTrue(pd_finding.source_found)
        self.assertEqual(pd_finding.status, "FOUND")
        self.assertEqual(pd_finding.source_records, 1)

    def test_05_target_playerdata_absent(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        pd_finding = next(f for f in insp.findings if f.adapter == "vanilla_playerdata")
        self.assertFalse(pd_finding.target_found)
        self.assertFalse(pd_finding.conflict)

    def test_06_source_and_target_present_collision(self):
        # Create target file to induce collision
        (self.root / f"world/playerdata/{self.canonical_uuid}.dat").write_bytes(b"targetdata")
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        pd_finding = next(f for f in insp.findings if f.adapter == "vanilla_playerdata")
        self.assertTrue(pd_finding.source_found)
        self.assertTrue(pd_finding.target_found)
        self.assertTrue(pd_finding.conflict)
        self.assertIn("TARGET_COLLISION", pd_finding.blocker)
        self.assertIn(pd_finding.blocker, insp.blockers)

    def test_07_advancements_found(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        f = next(f for f in insp.findings if f.adapter == "vanilla_advancements")
        self.assertTrue(f.source_found)
        self.assertEqual(f.status, "FOUND")

    def test_08_stats_found(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        f = next(f for f in insp.findings if f.adapter == "vanilla_stats")
        self.assertTrue(f.source_found)
        self.assertEqual(f.status, "FOUND")

    def test_09_auraskills_found(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        f = next(f for f in insp.findings if f.adapter == "auraskills")
        self.assertTrue(f.source_found)
        self.assertEqual(f.status, "FOUND")

    def test_10_quests_found(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        f = next(f for f in insp.findings if f.adapter == "quests")
        self.assertTrue(f.source_found)
        self.assertEqual(f.status, "FOUND")

    def test_11_mvi_found_with_cache_warning(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        f = next(f for f in insp.findings if f.adapter == "multiverse_inventories")
        self.assertTrue(f.source_found)
        self.assertEqual(f.source_records, 1)
        self.assertIsNotNone(f.warning)
        self.assertIn(f.warning, insp.warnings)

    def test_12_sqlite_select_read_only(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        hh = next(f for f in insp.findings if f.adapter == "huskhomes")
        self.assertTrue(hh.source_found)
        self.assertEqual(hh.source_records, 3)  # 2 saved_positions + 1 user
        self.assertFalse(hh.conflict)

    def test_13_multiple_records_sqlite_collision(self):
        # Insert record for canonical_uuid to induce SQL collision
        db_path = self.root / "plugins/HuskHomes/HuskHomesData.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO huskhomes_saved_positions (player_uuid) VALUES (?)", (self.canonical_uuid,))

        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        hh = next(f for f in insp.findings if f.adapter == "huskhomes")
        self.assertTrue(hh.conflict)
        self.assertIn("HUSKHOMES_TARGET_COLLISION", hh.blocker)

    def test_14_source_non_existent(self):
        empty_id = MigrationIdentity(
            discord_user_id="100",
            canonical_name="UnknownPlayer",
            canonical_uuid="99999999-9999-9999-9999-999999999999",
            legacy_uuid="88888888-8888-8888-8888-888888888888",
        )
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(empty_id)
        pd = next(f for f in insp.findings if f.adapter == "vanilla_playerdata")
        self.assertFalse(pd.source_found)
        self.assertEqual(pd.status, "NOT_FOUND")

    def test_15_conflict_becomes_blocker(self):
        (self.root / f"plugins/AuraSkills/userdata/{self.canonical_uuid}.yml").write_text("collision")
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        self.assertTrue(any("AURASKILLS_TARGET_COLLISION" in b for b in insp.blockers))

    def test_16_warning_does_not_become_blocker(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        self.assertTrue(len(insp.warnings) > 0)  # MVI cache warning
        self.assertEqual(len(insp.blockers), 0)  # No blockers!

    def test_17_plan_ready(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        planner = GenericPlanner()
        plan = planner.create_plan(insp)
        self.assertEqual(plan.status, "READY")
        self.assertTrue(plan.summary["files_count"] > 0)
        self.assertTrue(plan.summary["db_records_count"] > 0)
        self.assertEqual(plan.summary["blockers_count"], 0)

    def test_18_plan_blocked_on_collision(self):
        (self.root / f"world/playerdata/{self.canonical_uuid}.dat").write_bytes(b"collision")
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        planner = GenericPlanner()
        plan = planner.create_plan(insp)
        self.assertEqual(plan.status, "BLOCKED")
        self.assertTrue(len(plan.blockers) > 0)

    def test_19_plan_determinism(self):
        inspector = GenericInspector(self.root)
        insp1 = inspector.inspect(self.identity)
        insp2 = inspector.inspect(self.identity)
        planner = GenericPlanner()
        plan1 = planner.create_plan(insp1)
        plan2 = planner.create_plan(insp2)

        self.assertEqual(plan1.inspection_fingerprint, plan2.inspection_fingerprint)
        self.assertEqual(plan1.status, plan2.status)
        self.assertEqual(plan1.summary, plan2.summary)
        self.assertEqual(plan1.planned_changes, plan2.planned_changes)

    def test_20_fingerprint_changes_if_data_changes(self):
        inspector = GenericInspector(self.root)
        insp1 = inspector.inspect(self.identity)

        # Modify database
        db_path = self.root / "plugins/HuskHomes/HuskHomesData.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO huskhomes_saved_positions (player_uuid) VALUES (?)", (self.legacy_uuid,))

        insp2 = inspector.inspect(self.identity)
        self.assertNotEqual(insp1.inspection_fingerprint, insp2.inspection_fingerprint)

    def test_21_no_adapter_executes_write(self):
        # Collect hashes of all files in root before inspect/plan
        hashes_before = {}
        for p in self.root.rglob("*"):
            if p.is_file():
                hashes_before[str(p)] = p.stat().st_mtime_ns

        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        planner = GenericPlanner()
        planner.create_plan(insp)

        # Verify no file was modified
        for p in self.root.rglob("*"):
            if p.is_file():
                self.assertIn(str(p), hashes_before)
                self.assertEqual(p.stat().st_mtime_ns, hashes_before[str(p)])

    def test_22_paths_are_derived_internally(self):
        inspector = GenericInspector(self.root)
        insp = inspector.inspect(self.identity)
        for finding in insp.findings:
            if "source_path" in finding.details and finding.details["source_path"]:
                # Path must be relative to server root and not outside
                rel = Path(finding.details["source_path"])
                self.assertFalse(rel.is_absolute())
                self.assertFalse(".." in str(rel))


if __name__ == "__main__":
    unittest.main()
