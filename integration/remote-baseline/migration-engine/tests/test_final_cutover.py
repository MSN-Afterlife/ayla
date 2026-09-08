import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import yaml

import sys
sys.path.insert(0, "/opt/minecraft/migration-engine")

from lib import final_cutover
from lib.phase5g1 import BASE, ENGINE
from lib.ownership_policy import OwnershipPolicy

SUPPLEMENT = ENGINE / "supplements/phase5g2-imageframe"


class FinalCutoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ENGINE / "sandbox", prefix="final-cutover-test-")
        self.top = Path(self.tmp.name)
        self.root = self.top / "root"
        shutil.copytree(BASE, self.root)
        shutil.copytree(SUPPLEMENT, self.root, dirs_exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_final_identity_plan_targets_2eab_without_changing_legacy_default(self):
        with final_cutover.final_identity():
            plan = final_cutover.final_offline_plan(self.root)
        self.assertEqual(plan["status"], "READY_FOR_CONTROLLED_PILOT_PENDING_MAINTENANCE")
        vanilla = next(a for a in plan["adapters"] if a["name"] == "Vanilla")
        self.assertEqual(vanilla["target_records"], 0)

    def test_safe_empty_accepts_default_runtime_target(self):
        aura = self.root / "plugins/AuraSkills/userdata" / f"{final_cutover.TARGET}.yml"
        aura.write_text(
            yaml.safe_dump(
                {
                    "uuid": final_cutover.TARGET,
                    "skills": {"auraskills/mining": {"level": 0, "xp": 0.0}},
                    "mana": 20.0,
                    "action_bar": {"idle": True},
                },
                sort_keys=False,
            )
        )
        quests = self.root / "plugins/Quests/data" / f"{final_cutover.TARGET}.yml"
        quests.write_text(
            yaml.safe_dump(
                {"currentQuests": [], "currentStages": [], "quest-points": 0, "lastKnownName": final_cutover.TARGET_NAME},
                sort_keys=False,
            )
        )
        check = final_cutover.safe_empty_runtime_target_check(self.root)
        self.assertEqual(check["status"], "PASS")
        scrub = final_cutover.scrub_safe_empty_target(self.root)
        self.assertEqual(scrub["safe_empty_check"]["classification"], "SAFE_EMPTY_RUNTIME_TARGET")

    def test_non_empty_runtime_target_blocks(self):
        aura = self.root / "plugins/AuraSkills/userdata" / f"{final_cutover.TARGET}.yml"
        aura.write_text(
            yaml.safe_dump(
                {
                    "uuid": final_cutover.TARGET,
                    "skills": {"auraskills/mining": {"level": 1, "xp": 3.0}},
                    "mana": 20.0,
                },
                sort_keys=False,
            )
        )
        check = final_cutover.safe_empty_runtime_target_check(self.root)
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("AuraSkills target has skill progression", check["blockers"])

    def test_final_sandbox_apply_rollback_idempotency_and_70f_preservation(self):
        wrong = self.root / "plugins/AuraSkills/userdata" / f"{final_cutover.WRONG_TARGET}.yml"
        wrong.write_text("uuid: 70f73129-6bc7-47af-9248-d0f2ec3a891d\nskills: {}\n")
        result = final_cutover.run_final_sandbox(self.root, self.top / "final-sandbox")
        self.assertEqual(result["apply"], "PASS")
        self.assertEqual(result["rollback"], "PASS")
        self.assertEqual(result["idempotency"], "ALREADY_MIGRATED")
        self.assertEqual(result["collision_protection"], "PASS")
        self.assertEqual(result["ownership"], "PASS")
        self.assertEqual(result["wrong_target_70f_preservation"], "PASS")

    def test_plan_artifact_status_requires_sandbox_pass(self):
        plan = final_cutover.build_plan_artifact({"apply": "PASS"})
        self.assertEqual(plan["final_status"], "READY_FOR_FINAL_MAINTENANCE")
        self.assertEqual(plan["wrong_target_policy"], final_cutover.WRONG_TARGET_POLICY)


class ExecuteFinalCutoverTests(unittest.TestCase):
    """Tests for execute_final_cutover() — requires sandbox root; maintenance gate mocked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ENGINE / "sandbox", prefix="exec-test-")
        self.top = Path(self.tmp.name)
        self.root = self.top / "server-root"
        shutil.copytree(BASE, self.root)
        shutil.copytree(SUPPLEMENT, self.root, dirs_exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()
        import gc
        gc.collect()

    def _mock_maintenance(self):
        """Patch maintenance + paper-offline gates so we can test dry_run logic."""
        m_maint = mock.patch(
            "lib.final_cutover._assert_maintenance_mode",
            return_value=None,
        )
        m_paper = mock.patch(
            "lib.final_cutover._assert_paper_offline",
            return_value=None,
        )
        return m_maint, m_paper

    # ------------------------------------------------------------------ #
    # Test: root-owned snapshot restore must call chown(1000, 0)          #
    # This test verifies the ownership contract of the explicit policy    #
    # used in execute_final_cutover gate 5 (OWNERSHIP_SNAPSHOT) and      #
    # gate 7 (STAGING_CREATED). It does not require root privileges;     #
    # it captures chown calls and asserts uid=1000, gid=0.               #
    # ------------------------------------------------------------------ #
    def test_root_owned_snapshot_restore_chowns_to_uid1000_gid0(self):
        """
        OwnershipPolicy.explicit(root, 1000, 0).copytree must call os.chown(path, 1000, 0)
        on every path it creates. This is the contract verified at gates 5+7 of
        execute_final_cutover. The syscall may fail without root — we verify the call,
        not the outcome.
        """
        src = self.top / "src-for-chown"
        dst = self.top / "dst-for-chown"
        shutil.copytree(BASE, src)

        chown_calls = []
        real_chown = os.chown

        def capture_chown(path, uid, gid):
            chown_calls.append((Path(path).name, uid, gid))
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass  # verify the call intent, not the syscall success

        policy = OwnershipPolicy.explicit(src, final_cutover.EXPECTED_UID, final_cutover.EXPECTED_GID)
        with mock.patch("lib.ownership_policy.os.chown", side_effect=capture_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True):
            policy.copytree(src, dst)

        self.assertTrue(chown_calls, "Expected at least one chown call during copytree")
        for name, uid, gid in chown_calls:
            self.assertEqual(uid, final_cutover.EXPECTED_UID,
                             f"Expected UID 1000 on {name!r}, got {uid}")
            self.assertEqual(gid, final_cutover.EXPECTED_GID,
                             f"Expected GID 0 on {name!r}, got {gid}")

    # ------------------------------------------------------------------ #
    # Test: staging copy (gate 7) calls chown(1000, 0) on all paths       #
    # ------------------------------------------------------------------ #
    def test_copytree_to_staging_calls_chown_uid1000_gid0(self):
        """
        execute_final_cutover gate 7: policy.copytree(snap_root, staging) where
        policy = OwnershipPolicy.explicit(ENGINE, 1000, 0). Every path created
        must be passed to os.chown(path, 1000, 0).
        """
        src = self.top / "src-staging"
        dst = self.top / "dst-staging"
        shutil.copytree(BASE, src)

        chown_calls = []
        real_chown = os.chown

        def capture_chown(path, uid, gid):
            chown_calls.append((Path(path).name, uid, gid))
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        policy = OwnershipPolicy.explicit(ENGINE, final_cutover.EXPECTED_UID, final_cutover.EXPECTED_GID)
        with mock.patch("lib.ownership_policy.os.chown", side_effect=capture_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True):
            policy.copytree(src, dst)

        self.assertTrue(chown_calls, "Expected chown calls during staging copytree")
        for name, uid, gid in chown_calls:
            self.assertEqual(uid, final_cutover.EXPECTED_UID,
                             f"Expected UID 1000 on {name!r}, got {uid}")
            self.assertEqual(gid, final_cutover.EXPECTED_GID,
                             f"Expected GID 0 on {name!r}, got {gid}")



    # ------------------------------------------------------------------ #
    # Test: dry_run gates 1-13 all PASS                                   #
    # ------------------------------------------------------------------ #
    def test_execute_dry_run_all_gates_pass(self):
        m_maint, m_paper = self._mock_maintenance()
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True):
            result = final_cutover.execute_final_cutover(self.root, dry_run_only=True)

        self.assertEqual(result["final_status"], "DRY_RUN_COMPLETE")
        gate_names = [g["gate"] for g in result["gates"]]
        for required in [
            "MAINTENANCE_MODE",
            "PAPER_OFFLINE",
            "FINAL_SNAPSHOT",
            "SNAPSHOT_COMPLETENESS",
            "OWNERSHIP_SNAPSHOT",
            "SAFE_EMPTY_RUNTIME_TARGET",
            "STAGING_CREATED",
            "SCRUB_SAFE_EMPTY",
            "APPLY",
            "VERIFY",
            "SIMPLELOGIN_TRANSITION_PRESERVE",
            "OWNERSHIP_POST_APPLY",
            "WRONG_TARGET_70F_PRESERVATION",
            "DRY_RUN_COMPLETE",
        ]:
            self.assertIn(required, gate_names, f"Missing gate: {required}")
        pass_gates = [g for g in result["gates"] if g.get("status") not in ("PASS", "PRESERVED")]
        self.assertEqual(pass_gates, [], f"Non-PASS gate found: {pass_gates}")

    # ------------------------------------------------------------------ #
    # Test: maintenance gate blocks without maintenance.json               #
    # ------------------------------------------------------------------ #
    def test_execute_blocks_without_maintenance_mode(self):
        # Do NOT mock maintenance gate — it should fail naturally
        from lib.adapters import AdapterError
        with self.assertRaises((AdapterError, SystemExit)):
            final_cutover.execute_final_cutover(self.root, dry_run_only=True)

    # ------------------------------------------------------------------ #
    # Test: safe-empty gate blocks on real inventory                       #
    # ------------------------------------------------------------------ #
    def test_execute_dry_run_blocks_on_real_target_inventory(self):
        """If 2eab has real inventory in dat file, SAFE_EMPTY_RUNTIME_TARGET must FAIL."""
        from lib.adapters import AdapterError

        check_fail = {
            "status": "FAIL",
            "classification": "TARGET_HAS_RUNTIME_STATE",
            "target": final_cutover.TARGET,
            "evidence": [],
            "blockers": ["Vanilla target has inventory/ender/xp state"],
        }
        m_maint, m_paper = self._mock_maintenance()
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True), \
             mock.patch("lib.final_cutover.safe_empty_runtime_target_check", return_value=check_fail):
            with self.assertRaises(AdapterError) as ctx:
                final_cutover.execute_final_cutover(self.root, dry_run_only=True)
        self.assertIn("SAFE_EMPTY_RUNTIME_TARGET", str(ctx.exception))

    # ------------------------------------------------------------------ #
    # Test: SimpleLogin credential in staging causes FAIL                  #
    # ------------------------------------------------------------------ #
    def test_execute_dry_run_blocks_on_simplelogin_credential_for_target(self):
        from lib.adapters import AdapterError

        fake_rows = {
            "sl_accounts": [
                {
                    "uuid": final_cutover.TARGET,
                    "password_hash": "fakehash",
                    "name": final_cutover.TARGET_NAME,
                }
            ]
        }
        safe_pass = {
            "status": "PASS",
            "classification": "SAFE_EMPTY_RUNTIME_TARGET",
            "target": final_cutover.TARGET,
            "evidence": [],
            "blockers": [],
        }
        m_maint, m_paper = self._mock_maintenance()
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True), \
             mock.patch("lib.final_cutover.safe_empty_runtime_target_check", return_value=safe_pass), \
             mock.patch("lib.final_cutover._db_rows", return_value=fake_rows):
            with self.assertRaises(AdapterError) as ctx:
                final_cutover.execute_final_cutover(self.root, dry_run_only=True)
        self.assertIn("SIMPLELOGIN_TRANSITION_PRESERVE", str(ctx.exception))



    # ------------------------------------------------------------------ #
    # Test: 70f preservation gate blocks if 70f file changes              #
    # ------------------------------------------------------------------ #
    def test_execute_dry_run_blocks_on_70f_modification(self):
        from lib.adapters import AdapterError

        wrong_yml = self.root / "plugins/AuraSkills/userdata" / f"{final_cutover.WRONG_TARGET}.yml"
        wrong_yml.write_text(f"uuid: {final_cutover.WRONG_TARGET}\nskills: {{}}\n")

        m_maint, m_paper = self._mock_maintenance()
        original_assert = final_cutover._assert_wrong_target_intact
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        def tamper_then_assert(snap_root, staging):
            f = staging / "plugins/AuraSkills/userdata" / f"{final_cutover.WRONG_TARGET}.yml"
            if f.exists():
                f.write_text(
                    f"uuid: {final_cutover.WRONG_TARGET}\nskills: {{modified: true}}\n"
                )
            return original_assert(snap_root, staging)

        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True), \
             mock.patch("lib.final_cutover._assert_wrong_target_intact", side_effect=tamper_then_assert):
            with self.assertRaises(AdapterError) as ctx:
                final_cutover.execute_final_cutover(self.root, dry_run_only=True)
        self.assertIn("70F", str(ctx.exception).upper())

    # ------------------------------------------------------------------ #
    # Test: failure injection triggers rollback gate in result             #
    # ------------------------------------------------------------------ #
    def test_execute_dry_run_failure_after_snapshot_logs_rollback(self):
        from lib.adapters import AdapterError

        m_maint, m_paper = self._mock_maintenance()
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True), \
             mock.patch(
                 "lib.final_cutover.scrub_safe_empty_target",
                 side_effect=AdapterError("injected_test_failure"),
             ):
            with self.assertRaises(AdapterError) as ctx:
                final_cutover.execute_final_cutover(self.root, dry_run_only=True)
        self.assertIn("injected_test_failure", str(ctx.exception))
        # Result JSON must be written with ROLLBACK gates
        run_dirs = sorted(
            (ENGINE / "runs").glob("final-cutover-*"),
            key=lambda p: p.stat().st_mtime,
        )
        self.assertTrue(run_dirs, "Expected a run dir to be written on failure")
        result = json.loads((run_dirs[-1] / "result.json").read_text())
        self.assertEqual(result["final_status"], "FAILED")
        gate_names = [g["gate"] for g in result["gates"]]
        self.assertIn("ROLLBACK_TRIGGERED", gate_names)
        self.assertIn("ROLLBACK", gate_names)

    # ------------------------------------------------------------------ #
    # Test: idempotency — dry_run after dry_run still works               #
    # ------------------------------------------------------------------ #
    def test_execute_dry_run_idempotent_second_call_succeeds(self):
        m_maint, m_paper = self._mock_maintenance()
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        ctx = {
            "maint": m_maint,
            "paper": m_paper,
            "chown": mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown),
            "check_path": mock.patch.object(OwnershipPolicy, "check_path", return_value=True),
            "check_tree": mock.patch.object(OwnershipPolicy, "check_tree", return_value=True),
        }
        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True):
            r1 = final_cutover.execute_final_cutover(self.root, dry_run_only=True)
        self.assertEqual(r1["final_status"], "DRY_RUN_COMPLETE")

        # Second call — uses a new unique ts/run_id
        with m_maint, m_paper, \
             mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True), \
             mock.patch.object(OwnershipPolicy, "check_tree", return_value=True):
            r2 = final_cutover.execute_final_cutover(self.root, dry_run_only=True)
        self.assertEqual(r2["final_status"], "DRY_RUN_COMPLETE")
        self.assertNotEqual(r1["run_id"], r2["run_id"])

    # ------------------------------------------------------------------ #
    # Test: ownership mode preserved (mode bits intact after snapshot)    #
    # ------------------------------------------------------------------ #
    def test_ownership_mode_preserved_through_snapshot_copy(self):
        """File permission modes must not be altered by snapshot copy."""
        src_file = self.root / "plugins/AuraSkills/userdata" / f"{final_cutover.SOURCE}.yml"
        if not src_file.exists():
            self.skipTest("AuraSkills source file not present in base fixture")
        os.chmod(src_file, 0o644)

        dst = self.top / "snap-mode-test"
        real_chown = os.chown

        def lenient_chown(path, uid, gid):
            try:
                real_chown(path, uid, gid)
            except PermissionError:
                pass

        with mock.patch("lib.ownership_policy.os.chown", side_effect=lenient_chown), \
             mock.patch.object(OwnershipPolicy, "check_path", return_value=True):
            final_cutover.copy_final_snapshot(self.root, dst)

        dst_file = dst / "plugins/AuraSkills/userdata" / f"{final_cutover.SOURCE}.yml"
        if dst_file.exists():
            import stat
            copied_mode = stat.S_IMODE(dst_file.stat().st_mode)
            self.assertEqual(copied_mode, 0o644,
                             "Mode should be preserved as 0o644 after snapshot copy")



if __name__ == "__main__":
    unittest.main()
