from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .domain import MigrationIdentity, MigrationPlan, now_iso
from .executor import GenericMigrationRun, PRODUCTION_SERVER_ROOT, StalePlanError
from .lock import GatewayMigrationLock, PlayerPresenceChecker
from .rollback import GranularRollbackManager, RollbackConflictError
from .snapshot import SnapshotManifest
from .snapshot import GranularSnapshotManager


class Phase2BError(Exception):
    pass


class WriteDisabledError(Phase2BError):
    pass


class IdempotencyConflictError(Phase2BError):
    pass


class NonceError(Phase2BError):
    pass


class InvalidTransitionError(Phase2BError):
    pass


class Migration2BState(str, Enum):
    REQUESTED = "REQUESTED"
    PLANNED = "PLANNED"
    LOCKING = "LOCKING"
    LOCKED = "LOCKED"
    QUIESCING = "QUIESCING"
    SNAPSHOTTING = "SNAPSHOTTING"
    SNAPSHOT_VALID = "SNAPSHOT_VALID"
    APPLYING = "APPLYING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_PRE_APPLY = "FAILED_PRE_APPLY"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_CONFLICT = "ROLLBACK_CONFLICT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"


VALID_TRANSITIONS: dict[Migration2BState, set[Migration2BState]] = {
    Migration2BState.REQUESTED: {Migration2BState.PLANNED, Migration2BState.FAILED_PRE_APPLY},
    Migration2BState.PLANNED: {Migration2BState.LOCKING, Migration2BState.FAILED_PRE_APPLY},
    Migration2BState.LOCKING: {Migration2BState.LOCKED, Migration2BState.RECOVERY_REQUIRED, Migration2BState.FAILED_PRE_APPLY},
    Migration2BState.LOCKED: {Migration2BState.QUIESCING, Migration2BState.ROLLING_BACK, Migration2BState.RECOVERY_REQUIRED},
    Migration2BState.QUIESCING: {Migration2BState.SNAPSHOTTING, Migration2BState.ROLLING_BACK, Migration2BState.RECOVERY_REQUIRED},
    Migration2BState.SNAPSHOTTING: {Migration2BState.SNAPSHOT_VALID, Migration2BState.ROLLING_BACK, Migration2BState.RECOVERY_REQUIRED},
    Migration2BState.SNAPSHOT_VALID: {Migration2BState.APPLYING, Migration2BState.ROLLING_BACK},
    Migration2BState.APPLYING: {Migration2BState.VERIFYING, Migration2BState.ROLLING_BACK, Migration2BState.RECOVERY_REQUIRED},
    Migration2BState.VERIFYING: {Migration2BState.SUCCEEDED, Migration2BState.ROLLING_BACK, Migration2BState.RECOVERY_REQUIRED},
    Migration2BState.SUCCEEDED: {Migration2BState.ROLLING_BACK},
    Migration2BState.RECOVERY_REQUIRED: {Migration2BState.ROLLING_BACK},
    Migration2BState.CRITICAL_FAILURE: {Migration2BState.ROLLING_BACK},
    Migration2BState.ROLLBACK_CONFLICT: {Migration2BState.ROLLING_BACK},
    Migration2BState.ROLLING_BACK: {
        Migration2BState.ROLLED_BACK,
        Migration2BState.ROLLBACK_CONFLICT,
        Migration2BState.CRITICAL_FAILURE,
        Migration2BState.RECOVERY_REQUIRED,
    },
}

TERMINAL_STATES = {
    Migration2BState.SUCCEEDED,
    Migration2BState.FAILED_PRE_APPLY,
    Migration2BState.ROLLED_BACK,
    Migration2BState.ROLLBACK_CONFLICT,
    Migration2BState.CRITICAL_FAILURE,
}


class QuiescenceRequirement(str, Enum):
    NONE = "NONE"
    PLAYER_OFFLINE = "PLAYER_OFFLINE"
    PLAYER_OFFLINE_AND_DELAY = "PLAYER_OFFLINE_AND_DELAY"
    SERVER_OFFLINE = "SERVER_OFFLINE"
    CONSOLE_API_ONLY = "CONSOLE_API_ONLY"
    TRANSITION_PRESERVE = "TRANSITION_PRESERVE"


ADAPTER_QUIESCENCE_2B: dict[str, dict[str, Any]] = {
    "vanilla_playerdata": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "vanilla_advancements": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "vanilla_stats": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "auraskills": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "quests": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "multiverse_inventories": {
        "requirement": QuiescenceRequirement.PLAYER_OFFLINE_AND_DELAY.value,
        "delay_seconds": 5,
        "reason": "MVI keeps inventory state in memory; delay is explicit plan metadata, not a global sleep authority.",
    },
    "imageframe": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "huskhomes": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "elitemobs": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "waypoints": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "ultimateteams": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "simplepets": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "marriagemaster": {"requirement": QuiescenceRequirement.PLAYER_OFFLINE.value},
    "simplelogin": {"requirement": QuiescenceRequirement.TRANSITION_PRESERVE.value},
    "luckperms": {"requirement": QuiescenceRequirement.CONSOLE_API_ONLY.value},
}


@dataclass(frozen=True)
class FeatureFlags:
    write_enabled: bool
    production_enabled: bool

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        return cls(
            write_enabled=_strict_true(os.getenv("MIGRATION_WRITE_ENABLED")),
            production_enabled=_strict_true(os.getenv("MIGRATION_PRODUCTION_ENABLED")),
        )

    def assert_write_allowed(self, server_root: Path | str) -> None:
        resolved = Path(server_root).resolve()
        if not self.write_enabled:
            raise WriteDisabledError("MIGRATION_WRITE_ENABLED is not true.")
        if not self.production_enabled:
            raise WriteDisabledError("MIGRATION_PRODUCTION_ENABLED is not true.")
        if resolved == PRODUCTION_SERVER_ROOT or _same_or_alias(resolved, PRODUCTION_SERVER_ROOT):
            raise WriteDisabledError("Production server root remains blocked for Phase 2B.")


def _strict_true(value: str | None) -> bool:
    return value == "true"


def _same_or_alias(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AtomicJsonStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


class IdempotencyStore(AtomicJsonStore):
    def reserve_or_replay(self, key: str | None, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not key or not str(key).strip():
            raise Phase2BError("Idempotency-Key is required.")
        data = self.load()
        entry = data.get(key)
        digest = stable_payload_hash(payload)
        if entry:
            if entry.get("payload_hash") != digest:
                raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
            return entry.get("result")
        data[key] = {"payload_hash": digest, "created_at": now_iso(), "result": None}
        self.save(data)
        return None

    def store_result(self, key: str, result: dict[str, Any]) -> None:
        data = self.load()
        if key in data:
            data[key]["result"] = result
            self.save(data)


class ApprovalNonceStore(AtomicJsonStore):
    def issue(self, plan: MigrationPlan, operator_discord_id: str, *, action: str, ttl_seconds: int = 300) -> str:
        nonce = secrets.token_urlsafe(32)
        data = self.load()
        data[nonce] = {
            "plan_id": plan.plan_id,
            "plan_hash": stable_payload_hash(plan.to_dict()),
            "operator_discord_id": str(operator_discord_id),
            "action": action,
            "expires_at": time.time() + ttl_seconds,
            "used": False,
            "created_at": now_iso(),
        }
        self.save(data)
        return nonce

    def consume(self, nonce: str, plan: MigrationPlan, operator_discord_id: str, *, action: str) -> None:
        data = self.load()
        entry = data.get(nonce)
        if not entry:
            raise NonceError("INVALID_NONCE")
        if entry.get("used"):
            raise NonceError("NONCE_REPLAY")
        if float(entry.get("expires_at", 0)) < time.time():
            raise NonceError("EXPIRED_NONCE")
        if entry.get("operator_discord_id") != str(operator_discord_id):
            raise NonceError("NONCE_OPERATOR_MISMATCH")
        if entry.get("action") != action or entry.get("plan_id") != plan.plan_id:
            raise NonceError("NONCE_PLAN_MISMATCH")
        if not hmac.compare_digest(entry.get("plan_hash", ""), stable_payload_hash(plan.to_dict())):
            raise NonceError("NONCE_PLAN_MISMATCH")
        entry["used"] = True
        entry["used_at"] = now_iso()
        self.save(data)


class MigrationStateStore(AtomicJsonStore):
    def initialize(self, migration_id: str, identity: MigrationIdentity) -> None:
        data = self.load()
        data[migration_id] = {
            "state": Migration2BState.REQUESTED.value,
            "identity": identity.to_dict(),
            "pid": os.getpid(),
            "history": [(Migration2BState.REQUESTED.value, now_iso())],
        }
        self.save(data)

    def update_fields(self, migration_id: str, fields: dict[str, Any]) -> None:
        data = self.load()
        data.setdefault(migration_id, {}).update(fields)
        data[migration_id]["updated_at"] = now_iso()
        self.save(data)

    def transition(self, migration_id: str, new_state: Migration2BState) -> None:
        data = self.load()
        current = Migration2BState(data[migration_id]["state"])
        if new_state not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError(f"Invalid transition {current.value}->{new_state.value}")
        data[migration_id]["state"] = new_state.value
        data[migration_id].setdefault("history", []).append((new_state.value, now_iso()))
        self.save(data)

    def mark_recovery_required_for_dead_pids(self) -> list[str]:
        data = self.load()
        changed: list[str] = []
        for migration_id, entry in data.items():
            try:
                state = Migration2BState(entry.get("state"))
            except Exception:
                continue
            if state in TERMINAL_STATES or state == Migration2BState.RECOVERY_REQUIRED:
                continue
            pid = int(entry.get("pid", -1))
            if pid <= 0 or not _pid_alive(pid):
                entry["state"] = Migration2BState.RECOVERY_REQUIRED.value
                entry.setdefault("history", []).append((Migration2BState.RECOVERY_REQUIRED.value, now_iso()))
                changed.append(migration_id)
        if changed:
            self.save(data)
        return changed


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError as err:
        return getattr(err, "errno", None) != 3


class AuditLog:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        safe = {k: v for k, v in event.items() if k.lower() not in {"token", "password", "credentials", "nbt"}}
        safe["timestamp"] = safe.get("timestamp") or now_iso()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe, sort_keys=True, ensure_ascii=True) + "\n")


class Phase2BMigrationService:
    def __init__(
        self,
        server_root: Path | str,
        state_dir: Path | str,
        *,
        flags: FeatureFlags | None = None,
        presence_checker: PlayerPresenceChecker | None = None,
    ) -> None:
        self.server_root = Path(server_root).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.flags = flags or FeatureFlags.from_env()
        self.idempotency = IdempotencyStore(self.state_dir / "idempotency.json")
        self.nonces = ApprovalNonceStore(self.state_dir / "nonces.json")
        self.states = MigrationStateStore(self.state_dir / "migration_states.json")
        self.audit = AuditLog(self.state_dir / "audit.jsonl")
        self.gateway = GatewayMigrationLock(self.state_dir / "gateway-deny-list.json")
        self.presence_checker = presence_checker or PlayerPresenceChecker(self.server_root)

    def status(self, migration_id: str) -> dict[str, Any]:
        self.states.mark_recovery_required_for_dead_pids()
        state = self.states.load().get(migration_id)
        if not state:
            return {"migration_id": migration_id, "status": "NOT_FOUND"}
        current = state.get("state")
        return {
            "migration_id": migration_id,
            "plan_id": state.get("plan_id"),
            "identity": state.get("identity"),
            "state": current,
            "created_at": state.get("history", [[None, None]])[0][1],
            "updated_at": state.get("updated_at"),
            "verification_summary": state.get("verification_summary", {}),
            "rollback_availability": current in {"SUCCEEDED", "RECOVERY_REQUIRED", "CRITICAL_FAILURE", "ROLLBACK_CONFLICT"},
            "lock_status": state.get("lock_status", "UNKNOWN"),
            "recovery_required": current == "RECOVERY_REQUIRED",
            "safe_error_summary": state.get("safe_error_summary"),
        }

    def execute(self, request: dict[str, Any], *, idempotency_key: str | None) -> dict[str, Any]:
        replay = self.idempotency.reserve_or_replay(idempotency_key, request)
        if replay:
            return replay
        plan = MigrationPlan.from_dict(request["plan"])
        migration_id = str(request.get("migration_id") or f"mig-{secrets.token_hex(8)}")
        operator = str(request.get("operator_discord_id", ""))
        self.flags.assert_write_allowed(self.server_root)
        self.nonces.consume(str(request.get("approval_nonce", "")), plan, operator, action="execute")
        self.presence_checker.assert_player_offline(plan.identity)
        self.states.initialize(migration_id, plan.identity)
        self.states.update_fields(migration_id, {"plan_id": plan.plan_id, "plan": plan.to_dict(), "lock_status": "UNLOCKED"})
        self.states.transition(migration_id, Migration2BState.PLANNED)
        self.states.transition(migration_id, Migration2BState.LOCKING)
        self.gateway.lock(plan.identity, migration_id, Migration2BState.LOCKING.value)
        self.states.update_fields(migration_id, {"lock_status": "GATEWAY_LOCKED"})
        result: dict[str, Any]
        try:
            run = GenericMigrationRun(
                self.server_root,
                self.state_dir / "runs",
                self.state_dir / "locks",
                presence_checker=self.presence_checker,
                inject_failure_at=request.get("inject_failure_at"),
            )
            result = run.execute(migration_id, plan, allow_write=True)
            if result.get("status") != "SUCCEEDED":
                raise Phase2BError(result.get("error", result.get("status", "execute failed")))
            self.states.transition(migration_id, Migration2BState.LOCKED)
            self.states.transition(migration_id, Migration2BState.QUIESCING)
            self.states.transition(migration_id, Migration2BState.SNAPSHOTTING)
            self.states.transition(migration_id, Migration2BState.SNAPSHOT_VALID)
            self.states.transition(migration_id, Migration2BState.APPLYING)
            self.states.transition(migration_id, Migration2BState.VERIFYING)
            self.states.transition(migration_id, Migration2BState.SUCCEEDED)
            self.states.update_fields(migration_id, {"verification_summary": {"execute": "PASS"}})
            self.gateway.unlock(plan.identity)
            self.states.update_fields(migration_id, {"lock_status": "UNLOCKED"})
        except StalePlanError:
            self.states.transition(migration_id, Migration2BState.FAILED_PRE_APPLY)
            self.gateway.unlock(plan.identity)
            self.states.update_fields(migration_id, {"lock_status": "UNLOCKED"})
            result = {"migration_id": migration_id, "status": "PLAN_STALE"}
        except Exception as err:
            self.states.transition(migration_id, Migration2BState.RECOVERY_REQUIRED)
            self.states.update_fields(migration_id, {"safe_error_summary": str(err), "lock_status": "GATEWAY_LOCKED"})
            result = {"migration_id": migration_id, "status": "RECOVERY_REQUIRED", "error": str(err)}
        self.audit.record({
            "migration_id": migration_id,
            "plan_id": plan.plan_id,
            "identity_id": plan.identity.identity_id,
            "canonical_uuid": plan.identity.canonical_uuid,
            "legacy_uuid": plan.identity.legacy_uuid,
            "staff_discord_id": operator,
            "guild_id": request.get("guild_id"),
            "action": "execute",
            "request_id": request.get("request_id"),
            "idempotency_key": idempotency_key,
            "result": result.get("status"),
        })
        self.idempotency.store_result(str(idempotency_key), result)
        return result

    def rollback(self, migration_id: str, request: dict[str, Any], *, idempotency_key: str | None) -> dict[str, Any]:
        replay = self.idempotency.reserve_or_replay(idempotency_key, {"migration_id": migration_id, **request})
        if replay:
            return replay
        self.flags.assert_write_allowed(self.server_root)
        states = self.states.load()
        entry = states.get(migration_id)
        if not entry:
            raise Phase2BError("MIGRATION_NOT_FOUND")
        current = Migration2BState(entry.get("state"))
        if current == Migration2BState.ROLLED_BACK:
            result = {"migration_id": migration_id, "status": "ROLLED_BACK", "idempotent": True}
            self.idempotency.store_result(str(idempotency_key), result)
            return result
        if current not in {Migration2BState.SUCCEEDED, Migration2BState.RECOVERY_REQUIRED, Migration2BState.CRITICAL_FAILURE, Migration2BState.ROLLBACK_CONFLICT}:
            raise Phase2BError(f"ROLLBACK_STATE_INVALID:{current.value}")
        plan = MigrationPlan.from_dict(entry["plan"])
        operator = str(request.get("operator_discord_id", ""))
        self.nonces.consume(str(request.get("approval_nonce", "")), plan, operator, action="rollback")
        self.gateway.lock(plan.identity, migration_id, Migration2BState.ROLLING_BACK.value)
        self.states.update_fields(migration_id, {"lock_status": "GATEWAY_LOCKED"})
        self.presence_checker.assert_player_offline(plan.identity)
        self.states.transition(migration_id, Migration2BState.ROLLING_BACK)
        manifest_file = self.state_dir / "runs" / migration_id / "manifest.json"
        if not manifest_file.is_file():
            self.states.transition(migration_id, Migration2BState.RECOVERY_REQUIRED)
            raise Phase2BError("SNAPSHOT_MANIFEST_NOT_FOUND")
        manifest = SnapshotManifest.from_dict(json.loads(manifest_file.read_text(encoding="utf-8")))
        if manifest.status != "VALID":
            self.states.transition(migration_id, Migration2BState.RECOVERY_REQUIRED)
            raise Phase2BError(f"SNAPSHOT_NOT_VALID:{manifest.status}")
        try:
            self._assert_file_targets_match_applied_state(manifest)
            rb = GranularRollbackManager(self.state_dir / "runs")
            rb_result = rb.rollback(migration_id, self.server_root, manifest=manifest)
            verifier = GenericMigrationRun(
                self.server_root,
                self.state_dir / "runs",
                self.state_dir / "locks",
                presence_checker=self.presence_checker,
            )
            verifier._verify_rolled_back(plan, manifest)
            self.states.transition(migration_id, Migration2BState.ROLLED_BACK)
            self.gateway.unlock(plan.identity)
            self.states.update_fields(migration_id, {"lock_status": "UNLOCKED", "verification_summary": {"rollback": "PASS"}})
            result = {"migration_id": migration_id, "status": "ROLLED_BACK", "rollback": rb_result}
        except RollbackConflictError as err:
            self.states.transition(migration_id, Migration2BState.ROLLBACK_CONFLICT)
            self.states.update_fields(migration_id, {"safe_error_summary": str(err), "lock_status": "GATEWAY_LOCKED"})
            result = {"migration_id": migration_id, "status": "ROLLBACK_CONFLICT", "error": str(err)}
        except Exception as err:
            self.states.transition(migration_id, Migration2BState.CRITICAL_FAILURE)
            self.states.update_fields(migration_id, {"safe_error_summary": str(err), "lock_status": "GATEWAY_LOCKED"})
            result = {"migration_id": migration_id, "status": "CRITICAL_FAILURE", "error": str(err)}
        self.idempotency.store_result(str(idempotency_key), result)
        self.audit.record({"migration_id": migration_id, "action": "rollback", "idempotency_key": idempotency_key, "result": result["status"]})
        return result

    def _assert_file_targets_match_applied_state(self, manifest: SnapshotManifest) -> None:
        for entry in manifest.file_entries:
            if not entry.target_path_logical or not entry.source_sha256_before:
                continue
            target = self.server_root / entry.target_path_logical
            source = self.server_root / entry.source_path_logical if entry.source_path_logical else None
            if source and source.exists():
                continue
            if target.is_file() and GranularSnapshotManager.sha256_file(target) != entry.source_sha256_before:
                raise RollbackConflictError(
                    f"ROLLBACK_CONFLICT: Arquivo alvo {entry.target_path_logical} divergiu do conteúdo aplicado."
                )
