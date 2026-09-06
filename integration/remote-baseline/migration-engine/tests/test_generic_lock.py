from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from lib.generic.domain import MigrationIdentity
from lib.generic.lock import (
    GatewayLockSimulator,
    LockAcquisitionError,
    PlayerMigrationLock,
    PlayerOnlineError,
    PlayerPresenceChecker,
    StaleLockError,
)


class PlayerLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.locks_dir = Path(self.tmp.name) / "locks"
        self.locks_dir.mkdir()

        self.leg_u = "11111111-1111-1111-1111-111111111111"
        self.can_u = "22222222-2222-2222-2222-222222222222"

        self.identity1 = MigrationIdentity(
            discord_user_id="1001",
            canonical_name="PlayerOne",
            canonical_uuid=self.can_u,
            legacy_name="OldOne",
            legacy_uuid=self.leg_u,
        )

        self.identity2 = MigrationIdentity(
            discord_user_id="1002",
            canonical_name="PlayerTwo",
            canonical_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            legacy_name="OldTwo",
            legacy_uuid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        )

        self.lock_mgr = PlayerMigrationLock(self.locks_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_23_gateway_lock_normal_status(self):
        for st in ("PENDING", "PLANNED", "MIGRATED", "FAILED"):
            allowed, msg = GatewayLockSimulator.evaluate_login(st)
            self.assertTrue(allowed)
            self.assertIsNone(msg)

    def test_24_gateway_lock_migrating(self):
        allowed, msg = GatewayLockSimulator.evaluate_login("MIGRATING")
        self.assertFalse(allowed)
        self.assertIn("passando por uma migração", msg)

    def test_25_gateway_lock_rolling_back(self):
        allowed, msg = GatewayLockSimulator.evaluate_login("ROLLING_BACK")
        self.assertFalse(allowed)
        self.assertIn("passando por uma migração", msg)

    def test_26_lock_local_source(self):
        self.lock_mgr.acquire("mig-26", self.identity1)
        src_lock = self.locks_dir / f"{self.leg_u}.lock"
        self.assertTrue(src_lock.is_file())
        info = self.lock_mgr.inspect_lock(self.leg_u)
        self.assertIsNotNone(info)
        self.assertEqual(info.migration_id, "mig-26")

    def test_27_lock_local_target(self):
        self.lock_mgr.acquire("mig-27", self.identity1)
        tgt_lock = self.locks_dir / f"{self.can_u}.lock"
        self.assertTrue(tgt_lock.is_file())
        info = self.lock_mgr.inspect_lock(self.can_u)
        self.assertIsNotNone(info)
        self.assertEqual(info.migration_id, "mig-27")

    def test_28_duplicate_execution_same_identity_blocked(self):
        self.lock_mgr.acquire("mig-28-first", self.identity1)
        with self.assertRaises(LockAcquisitionError) as ctx:
            self.lock_mgr.acquire("mig-28-second", self.identity1)
        self.assertIn("LOCK_ACQUISITION_FAILED", str(ctx.exception))

    def test_29_two_different_identities_allowed(self):
        self.lock_mgr.acquire("mig-29a", self.identity1)
        # Identity 2 should acquire cleanly
        paths2 = self.lock_mgr.acquire("mig-29b", self.identity2)
        self.assertEqual(len(paths2), 2)

    def test_30_stale_lock_detected_when_pid_dead(self):
        # Create a fake lock file pointing to an impossible PID (e.g. 99999999)
        fake_uuid = "44444444-4444-4444-4444-444444444444"
        lock_file = self.locks_dir / f"{fake_uuid}.lock"
        payload = {
            "migration_id": "mig-old",
            "identity_id": "1",
            "canonical_uuid": fake_uuid,
            "legacy_uuid": "old",
            "created_at": "2020-01-01T00:00:00Z",
            "pid": 9999999,  # Non-existent PID
        }
        lock_file.write_text(json.dumps(payload))

        is_stale, reason = self.lock_mgr.check_stale(fake_uuid)
        self.assertTrue(is_stale)
        self.assertIn("STALE_DEAD_PID", reason)

    def test_31_active_or_ambiguous_lock_not_force_removed(self):
        # Current process PID is definitely alive!
        self.lock_mgr.acquire("mig-31", self.identity1)
        is_stale, reason = self.lock_mgr.check_stale(self.can_u)
        self.assertFalse(is_stale)

        with self.assertRaises(StaleLockError):
            self.lock_mgr.force_recover_stale_lock(self.can_u)
        self.assertTrue(self.lock_mgr.is_locked(self.can_u))

    def test_32_player_online_blocks_execution(self):
        checker = PlayerPresenceChecker(online_override={self.can_u})
        self.assertTrue(checker.is_player_online(self.identity1))
        with self.assertRaises(PlayerOnlineError):
            checker.assert_player_offline(self.identity1)

    def test_33_player_offline_allows_proceeding(self):
        checker = PlayerPresenceChecker(online_override=set())
        self.assertFalse(checker.is_player_online(self.identity1))
        # Should not raise
        checker.assert_player_offline(self.identity1)

    def test_34_lock_released_cleanly(self):
        self.lock_mgr.acquire("mig-34", self.identity1)
        self.assertTrue(self.lock_mgr.is_locked(self.can_u))
        self.assertTrue(self.lock_mgr.is_locked(self.leg_u))

        self.lock_mgr.release(self.identity1)
        self.assertFalse(self.lock_mgr.is_locked(self.can_u))
        self.assertFalse(self.lock_mgr.is_locked(self.leg_u))
