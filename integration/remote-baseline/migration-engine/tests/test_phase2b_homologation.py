from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon.server import DEFAULT_STATE_DIR, TCPThreadingHTTPServer, make_handler
from lib.generic.domain import MigrationIdentity
from lib.generic.inspector import GenericInspector
from lib.generic.lock import (
    GatewayMigrationLock,
    GatewayLockSimulator,
    PlayerPresenceChecker,
    PlayerPresenceUnknownError,
    PresenceState,
)
from lib.generic.phase2b import (
    ApprovalNonceStore,
    FeatureFlags,
    IdempotencyConflictError,
    IdempotencyStore,
    InvalidTransitionError,
    Migration2BState,
    MigrationStateStore,
    Phase2BMigrationService,
    QuiescenceRequirement,
    ADAPTER_QUIESCENCE_2B,
)
from lib.generic.planner import GenericPlanner
from lib.generic.rollback import GranularRollbackManager, RollbackError
from lib.generic.snapshot import SnapshotManifest


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class Phase2BHomologationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "server"
        self.state = Path(self.tmp.name) / "state"
        self.root.mkdir()
        self.state.mkdir()
        self.identity = MigrationIdentity(
            discord_user_id="1001",
            canonical_name="PlayerOne",
            canonical_uuid="22222222-2222-2222-2222-222222222222",
            legacy_name="OldOne",
            legacy_uuid="11111111-1111-1111-1111-111111111111",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_presence_without_real_authority_is_unknown_and_blocks(self):
        checker = PlayerPresenceChecker(self.root)
        self.assertEqual(checker.presence_state(self.identity), PresenceState.UNKNOWN)
        with self.assertRaises(PlayerPresenceUnknownError):
            checker.assert_player_offline(self.identity)

    def test_presence_override_models_online_and_offline_confirmed(self):
        self.assertEqual(
            PlayerPresenceChecker(online_override=set()).presence_state(self.identity),
            PresenceState.OFFLINE_CONFIRMED,
        )
        self.assertEqual(
            PlayerPresenceChecker(online_override={self.identity.canonical_uuid}).presence_state(self.identity),
            PresenceState.ONLINE,
        )

    def test_gateway_blocks_required_states_and_persists_per_identity(self):
        for state in ("LOCKING", "MIGRATING", "ROLLING_BACK", "RECOVERY_REQUIRED", "CRITICAL_FAILURE"):
            allowed, _ = GatewayLockSimulator.evaluate_login(state)
            self.assertFalse(allowed)
        lock = GatewayMigrationLock(self.state / "deny.json")
        lock.lock(self.identity, "mig-1", "LOCKING")
        self.assertFalse(lock.evaluate_login(self.identity.canonical_uuid)[0])
        self.assertFalse(lock.evaluate_login(self.identity.legacy_uuid)[0])
        self.assertTrue(lock.evaluate_login("unrelated-player", "PENDING")[0])

    def test_feature_flags_are_false_unless_exact_true_and_block_production_root(self):
        for value in (None, "", "TRUE", "1", "yes", "false"):
            self.assertFalse(FeatureFlags(_strict(value), False).write_enabled)
        flags = FeatureFlags(write_enabled=False, production_enabled=False)
        with self.assertRaises(Exception):
            flags.assert_write_allowed(self.root)

    def test_quiescence_catalog_declares_explicit_adapter_requirements(self):
        self.assertEqual(
            ADAPTER_QUIESCENCE_2B["multiverse_inventories"]["requirement"],
            QuiescenceRequirement.PLAYER_OFFLINE_AND_DELAY.value,
        )
        self.assertEqual(ADAPTER_QUIESCENCE_2B["luckperms"]["requirement"], QuiescenceRequirement.CONSOLE_API_ONLY.value)
        self.assertEqual(ADAPTER_QUIESCENCE_2B["simplelogin"]["requirement"], QuiescenceRequirement.TRANSITION_PRESERVE.value)

    def test_idempotency_replay_and_payload_conflict(self):
        store = IdempotencyStore(self.state / "idem.json")
        payload = {"a": 1}
        self.assertIsNone(store.reserve_or_replay("k1", payload))
        store.store_result("k1", {"status": "SAME"})
        self.assertEqual(store.reserve_or_replay("k1", payload), {"status": "SAME"})
        with self.assertRaises(IdempotencyConflictError):
            store.reserve_or_replay("k1", {"a": 2})

    def test_nonce_is_plan_operator_ttl_and_single_use_bound(self):
        plan = self._plan()
        store = ApprovalNonceStore(self.state / "nonces.json")
        nonce = store.issue(plan, "staff-1", action="execute", ttl_seconds=60)
        store.consume(nonce, plan, "staff-1", action="execute")
        with self.assertRaises(Exception):
            store.consume(nonce, plan, "staff-1", action="execute")
        expired = store.issue(plan, "staff-1", action="execute", ttl_seconds=-1)
        with self.assertRaises(Exception):
            store.consume(expired, plan, "staff-1", action="execute")
        wrong = store.issue(plan, "staff-1", action="execute", ttl_seconds=60)
        with self.assertRaises(Exception):
            store.consume(wrong, plan, "staff-2", action="execute")

    def test_state_machine_rejects_invalid_transition_and_marks_dead_pid_recovery_required(self):
        store = MigrationStateStore(self.state / "states.json")
        store.initialize("mig-1", self.identity)
        with self.assertRaises(InvalidTransitionError):
            store.transition("mig-1", Migration2BState.SUCCEEDED)
        data = store.load()
        data["mig-1"]["pid"] = 99999999
        store.save(data)
        self.assertEqual(store.mark_recovery_required_for_dead_pids(), ["mig-1"])
        self.assertEqual(store.load()["mig-1"]["state"], "RECOVERY_REQUIRED")

    def test_write_service_blocks_when_flags_false_before_mutation(self):
        plan = self._plan()
        nonce_store = ApprovalNonceStore(self.state / "nonces.json")
        nonce = nonce_store.issue(plan, "staff-1", action="execute")
        service = Phase2BMigrationService(
            self.root,
            self.state,
            flags=FeatureFlags(write_enabled=False, production_enabled=False),
            presence_checker=PlayerPresenceChecker(online_override=set()),
        )
        request = {"plan": plan.to_dict(), "approval_nonce": nonce, "operator_discord_id": "staff-1"}
        with self.assertRaises(Exception):
            service.execute(request, idempotency_key="exec-1")

    def test_sqlite_concurrency_begin_immediate_fails_safely_on_locked_db(self):
        db = self.root / "locked.db"
        conn1 = sqlite3.connect(db)
        conn1.execute("CREATE TABLE rows (uuid TEXT PRIMARY KEY, value TEXT)")
        conn1.execute("INSERT INTO rows VALUES (?, 'before')", (self.identity.legacy_uuid,))
        conn1.commit()
        conn1.execute("BEGIN IMMEDIATE")
        try:
            with sqlite3.connect(db, timeout=0.05) as conn2:
                with self.assertRaises(sqlite3.OperationalError):
                    conn2.execute("BEGIN IMMEDIATE")
        finally:
            conn1.rollback()
            conn1.close()

    def test_daemon_write_endpoints_exist_but_are_disabled_by_default(self):
        token = "phase2b-token-123456789"
        port = find_free_port()
        handler_cls = make_handler(self.root, token)
        server = TCPThreadingHTTPServer(("127.0.0.1", port), handler_cls)
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.05)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("POST", "/api/v1/migrations/execute", body=b"{}", headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Idempotency-Key": "api-1",
            })
            response = conn.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 403)
            self.assertEqual(body["code"], "MIGRATION_WRITE_DISABLED")
            conn.close()

            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/api/v1/migrations/mig-missing", headers={"Authorization": f"Bearer {token}"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read().decode("utf-8"))["status"], "NOT_FOUND")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_manifest_lifecycle_creating_to_valid_and_apply_accepts_valid(self):
        plan = self._plan()
        service = Phase2BMigrationService(
            self.root,
            self.state,
            flags=FeatureFlags(write_enabled=True, production_enabled=True),
            presence_checker=PlayerPresenceChecker(online_override=set()),
        )
        nonce = service.nonces.issue(plan, "staff-1", action="execute")
        result = service.execute(
            {"plan": plan.to_dict(), "approval_nonce": nonce, "operator_discord_id": "staff-1"},
            idempotency_key="exec-valid",
        )
        self.assertEqual(result["status"], "SUCCEEDED")
        manifest = json.loads((self.state / "runs" / result["migration_id"] / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "VALID")
        self.assertTrue(manifest["validation_results"]["valid"])

    def test_invalid_manifest_is_refused_by_rollback(self):
        plan = self._plan()
        service = Phase2BMigrationService(
            self.root,
            self.state,
            flags=FeatureFlags(write_enabled=True, production_enabled=True),
            presence_checker=PlayerPresenceChecker(online_override=set()),
        )
        nonce = service.nonces.issue(plan, "staff-1", action="execute")
        result = service.execute(
            {"plan": plan.to_dict(), "approval_nonce": nonce, "operator_discord_id": "staff-1"},
            idempotency_key="exec-invalid-manifest",
        )
        manifest_path = self.state / "runs" / result["migration_id"] / "manifest.json"
        manifest = SnapshotManifest.from_dict(json.loads(manifest_path.read_text()))
        manifest.status = "INVALID"
        manifest_path.write_text(json.dumps(manifest.to_dict()))
        with self.assertRaises(RollbackError):
            GranularRollbackManager(self.state / "runs").rollback(result["migration_id"], self.root)

    def test_public_rollback_orchestrates_from_persisted_state_and_unlocks(self):
        plan = self._plan()
        service = Phase2BMigrationService(
            self.root,
            self.state,
            flags=FeatureFlags(write_enabled=True, production_enabled=True),
            presence_checker=PlayerPresenceChecker(online_override=set()),
        )
        execute_nonce = service.nonces.issue(plan, "staff-1", action="execute")
        result = service.execute(
            {"plan": plan.to_dict(), "approval_nonce": execute_nonce, "operator_discord_id": "staff-1"},
            idempotency_key="exec-rollback",
        )
        rollback_nonce = service.nonces.issue(plan, "staff-1", action="rollback")
        rb = service.rollback(
            result["migration_id"],
            {"approval_nonce": rollback_nonce, "operator_discord_id": "staff-1"},
            idempotency_key="rollback-1",
        )
        self.assertEqual(rb["status"], "ROLLED_BACK")
        self.assertEqual(service.status(result["migration_id"])["state"], "ROLLED_BACK")
        self.assertTrue((self.root / "world/players/data" / f"{self.identity.legacy_uuid}.dat").is_file())
        self.assertTrue(service.gateway.evaluate_login(self.identity.canonical_uuid)[0])

    def test_public_rollback_conflict_keeps_gateway_lock(self):
        plan = self._plan()
        service = Phase2BMigrationService(
            self.root,
            self.state,
            flags=FeatureFlags(write_enabled=True, production_enabled=True),
            presence_checker=PlayerPresenceChecker(online_override=set()),
        )
        execute_nonce = service.nonces.issue(plan, "staff-1", action="execute")
        result = service.execute(
            {"plan": plan.to_dict(), "approval_nonce": execute_nonce, "operator_discord_id": "staff-1"},
            idempotency_key="exec-conflict",
        )
        target = self.root / "world/players/data" / f"{self.identity.canonical_uuid}.dat"
        target.write_bytes(b"changed-after-migration")
        rollback_nonce = service.nonces.issue(plan, "staff-1", action="rollback")
        rb = service.rollback(
            result["migration_id"],
            {"approval_nonce": rollback_nonce, "operator_discord_id": "staff-1"},
            idempotency_key="rollback-conflict",
        )
        self.assertIn(rb["status"], {"ROLLBACK_CONFLICT", "CRITICAL_FAILURE"})
        self.assertFalse(service.gateway.evaluate_login(self.identity.canonical_uuid)[0])

    def test_presence_authority_timeout_and_invalid_response_are_unknown(self):
        checker = PlayerPresenceChecker(authority_url="http://127.0.0.1:9/presence", timeout_seconds=0.05)
        self.assertEqual(checker.presence_state(self.identity), PresenceState.UNKNOWN)

    def test_presence_authority_online_and_offline_confirmed(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        states = ["ONLINE", "OFFLINE_CONFIRMED"]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):
                body = json.dumps({"presence_state": states.pop(0)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        port = find_free_port()
        server = HTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            checker = PlayerPresenceChecker(authority_url=f"http://127.0.0.1:{port}", timeout_seconds=1.0)
            self.assertEqual(checker.presence_state(self.identity), PresenceState.ONLINE)
            self.assertEqual(checker.presence_state(self.identity), PresenceState.OFFLINE_CONFIRMED)
        finally:
            server.shutdown()
            server.server_close()

    def test_gateway_lock_endpoint_reports_persistent_lock(self):
        token = "phase2b-token-123456789"
        port = find_free_port()
        service = Phase2BMigrationService(self.root, DEFAULT_STATE_DIR)
        service.gateway.lock(self.identity, "mig-endpoint", "LOCKING")
        handler_cls = make_handler(self.root, token)
        server = TCPThreadingHTTPServer(("127.0.0.1", port), handler_cls)
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.05)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", f"/api/v1/gateway/lock?key={self.identity.canonical_uuid}", headers={"Authorization": f"Bearer {token}"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read().decode("utf-8"))["locked"])
            conn.close()
        finally:
            service.gateway.unlock(self.identity)
            server.shutdown()
            server.server_close()

    def _plan(self):
        (self.root / "world/players/data").mkdir(parents=True, exist_ok=True)
        (self.root / "world/players/data" / f"{self.identity.legacy_uuid}.dat").write_bytes(b"player")
        return GenericPlanner().create_plan(GenericInspector(self.root).inspect(self.identity))


def _strict(value):
    with patch.dict(os.environ, {}, clear=True):
        if value is not None:
            os.environ["MIGRATION_WRITE_ENABLED"] = value
        return FeatureFlags.from_env().write_enabled


if __name__ == "__main__":
    unittest.main()
