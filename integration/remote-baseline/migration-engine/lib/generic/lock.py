from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .domain import MigrationIdentity, now_iso


class LockError(Exception):
    pass


class LockAcquisitionError(LockError):
    pass


class StaleLockError(LockError):
    pass


class PlayerOnlineError(LockError):
    pass


class PlayerPresenceUnknownError(PlayerOnlineError):
    pass


class PresenceState(str, Enum):
    OFFLINE_CONFIRMED = "OFFLINE_CONFIRMED"
    ONLINE = "ONLINE"
    UNKNOWN = "UNKNOWN"


@dataclass
class LockInfo:
    migration_id: str
    identity_id: str | None
    canonical_uuid: str
    legacy_uuid: str
    created_at: str
    pid: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockInfo:
        return cls(**data)


class PlayerMigrationLock:
    """Manages physical filesystem locks for player identities to ensure exclusive operations."""

    def __init__(self, locks_dir: Path | str) -> None:
        self.locks_dir = Path(locks_dir).resolve()
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def acquire(self, migration_id: str, identity: MigrationIdentity) -> list[Path]:
        can_u = identity.canonical_uuid
        leg_u = identity.legacy_uuid
        if not can_u or not leg_u:
            raise LockAcquisitionError("Identidade incompleta para aquisição de lock.")

        lock_files = [
            self.locks_dir / f"{can_u}.lock",
            self.locks_dir / f"{leg_u}.lock",
        ]

        info = LockInfo(
            migration_id=migration_id,
            identity_id=str(identity.identity_id) if identity.identity_id else None,
            canonical_uuid=can_u,
            legacy_uuid=leg_u,
            created_at=now_iso(),
            pid=os.getpid(),
        )
        payload = json.dumps(info.to_dict(), indent=2).encode("utf-8")

        acquired_paths: list[Path] = []
        try:
            for p in lock_files:
                self._create_exclusive(p, payload)
                acquired_paths.append(p)
        except Exception as err:
            # Rollback any acquired lock file on partial failure
            for p in acquired_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            raise err

        return acquired_paths

    def release(self, identity: MigrationIdentity) -> None:
        can_u = identity.canonical_uuid
        leg_u = identity.legacy_uuid
        targets = []
        if can_u:
            targets.append(self.locks_dir / f"{can_u}.lock")
        if leg_u:
            targets.append(self.locks_dir / f"{leg_u}.lock")

        for p in targets:
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass

    def inspect_lock(self, uuid_str: str) -> LockInfo | None:
        lock_file = self.locks_dir / f"{uuid_str}.lock"
        if not lock_file.is_file():
            return None
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            return LockInfo.from_dict(data)
        except Exception:
            return None

    def is_locked(self, uuid_str: str) -> bool:
        return (self.locks_dir / f"{uuid_str}.lock").is_file()

    def check_stale(self, uuid_str: str) -> tuple[bool, str]:
        """Check if lock exists and if the owning PID is dead."""
        info = self.inspect_lock(uuid_str)
        if not info:
            return False, "NO_LOCK"

        pid = info.pid
        if pid <= 0:
            return True, f"STALE_INVALID_PID_{pid}"

        try:
            # Signal 0 checks if process exists without killing it
            os.kill(pid, 0)
            return False, f"ACTIVE_PID_{pid}"
        except OSError as err:
            if err.errno == 3:  # ESRCH: No such process
                return True, f"STALE_DEAD_PID_{pid}"
            # EPERM: process exists but owned by another user -> alive
            return False, f"ACTIVE_OTHER_USER_PID_{pid}"

    def force_recover_stale_lock(self, uuid_str: str) -> bool:
        """Removes lock ONLY if confirmed stale (PID is dead). Rejects ambiguous locks."""
        is_stale, reason = self.check_stale(uuid_str)
        if not is_stale:
            raise StaleLockError(f"Não é permitido remover lock ativo ou ambíguo: {reason}")
        lock_file = self.locks_dir / f"{uuid_str}.lock"
        if lock_file.is_file():
            lock_file.unlink()
            return True
        return False

    def _create_exclusive(self, path: Path, payload: bytes) -> None:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            # Check who holds it
            info = self.inspect_lock(path.stem)
            holder = f"(PID {info.pid}, migration {info.migration_id})" if info else "(unknown)"
            raise LockAcquisitionError(
                f"LOCK_ACQUISITION_FAILED: Identidade já está bloqueada por outra migração {holder} em {path.name}"
            )


class GatewayLockSimulator:
    """Simulates or evaluates gateway (AylaAuth / Velocity) login permission based on migration status."""

    BLOCKING_STATES = frozenset(["LOCKING", "MIGRATING", "ROLLING_BACK", "RECOVERY_REQUIRED", "CRITICAL_FAILURE"])
    FRIENDLY_DENIAL_MESSAGE = "Sua conta está passando por uma migração de dados. Aguarde alguns instantes e tente novamente."

    @classmethod
    def evaluate_login(cls, migration_status: str) -> tuple[bool, str | None]:
        status_upper = (migration_status or "PENDING").upper()
        if status_upper in cls.BLOCKING_STATES:
            return False, cls.FRIENDLY_DENIAL_MESSAGE
        return True, None


class GatewayMigrationLock:
    """Persistent per-identity gateway deny-list for AylaAuth/Velocity staging integration."""

    def __init__(self, path: Path | str, ttl_seconds: int = 86400) -> None:
        self.path = Path(path).resolve()
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def lock(self, identity: MigrationIdentity, migration_id: str, state: str) -> None:
        data = self._load()
        now = time.time()
        for key in self._keys(identity):
            data[key] = {
                "migration_id": migration_id,
                "state": state,
                "locked_at": now,
                "expires_at": now + self.ttl_seconds,
            }
        self._write(data)

    def unlock(self, identity: MigrationIdentity) -> None:
        data = self._load()
        for key in self._keys(identity):
            data.pop(key, None)
        self._write(data)

    def evaluate_login(self, identity_key: str, api_status: str | None = None) -> tuple[bool, str | None]:
        data = self._load()
        now = time.time()
        entry = data.get(str(identity_key).lower())
        if entry and float(entry.get("expires_at", 0)) > now:
            return False, GatewayLockSimulator.FRIENDLY_DENIAL_MESSAGE
        allowed, msg = GatewayLockSimulator.evaluate_login(api_status or "PENDING")
        return allowed, msg

    def _keys(self, identity: MigrationIdentity) -> set[str]:
        vals = {
            identity.canonical_uuid,
            identity.legacy_uuid,
            str(identity.identity_id) if identity.identity_id is not None else None,
            identity.discord_user_id,
        }
        for acc in identity.accounts:
            vals.add(acc.get("external_id"))
            vals.add(acc.get("current_username"))
        return {str(v).lower() for v in vals if v}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


class PlayerPresenceChecker:
    """Verifies whether a player is currently online on the server."""

    def __init__(
        self,
        server_root: Path | str | None = None,
        *,
        online_override: set[str] | None = None,
        authority_url: str | None = None,
        authority_token: str | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.server_root = Path(server_root).resolve() if server_root else None
        self._online_override = set(online_override) if online_override is not None else None
        self.authority_url = authority_url or os.getenv("MIGRATION_PRESENCE_AUTHORITY_URL")
        self.authority_token = authority_token or os.getenv("MIGRATION_PRESENCE_AUTHORITY_TOKEN")
        self.timeout_seconds = timeout_seconds

    def presence_state(self, identity: MigrationIdentity) -> PresenceState:
        if self._online_override is not None:
            cand = {
                identity.canonical_uuid,
                identity.legacy_uuid,
                identity.canonical_name,
                identity.legacy_name,
            }
            cand = {c for c in cand if c}
            return PresenceState.ONLINE if self._online_override.intersection(cand) else PresenceState.OFFLINE_CONFIRMED

        if self.authority_url:
            return self._query_authority(identity)

        # No real authoritative presence source is wired in this daemon yet.
        # Playerdata files are deliberately not treated as filesystem locks.
        return PresenceState.UNKNOWN

    def _query_authority(self, identity: MigrationIdentity) -> PresenceState:
        body = json.dumps({"identity": identity.to_dict()}).encode("utf-8")
        req = urllib.request.Request(
            self.authority_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if self.authority_token:
            req.add_header("Authorization", f"Bearer {self.authority_token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as res:
                if res.status != 200:
                    return PresenceState.UNKNOWN
                data = json.loads(res.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return PresenceState.UNKNOWN
        state = str(data.get("presence_state", "")).upper()
        try:
            return PresenceState(state)
        except ValueError:
            return PresenceState.UNKNOWN

    def is_player_online(self, identity: MigrationIdentity) -> bool:
        return self.presence_state(identity) == PresenceState.ONLINE

    def assert_player_offline(self, identity: MigrationIdentity) -> None:
        state = self.presence_state(identity)
        if state == PresenceState.UNKNOWN:
            raise PlayerPresenceUnknownError(
                f"BLOCKED_PRESENCE_UNKNOWN: Não há autoridade real confirmando que {identity.canonical_name} está offline."
            )
        if state == PresenceState.ONLINE:
            raise PlayerOnlineError(
                f"BLOCKED_PLAYER_ONLINE: O jogador {identity.canonical_name} está online no servidor. "
                "A migração exige que o jogador esteja completamente desconectado."
            )


@dataclass(frozen=True)
class AdapterQuiescenceRequirement:
    adapter: str
    requires_player_offline: bool
    requires_paper_offline: bool
    requires_cache_flush: bool
    requires_console_command: str | None = None
    notes: str = ""


ADAPTER_QUIESCENCE_CATALOG: dict[str, AdapterQuiescenceRequirement] = {
    "vanilla_playerdata": AdapterQuiescenceRequirement("vanilla_playerdata", True, False, False, None, "Player offline"),
    "vanilla_advancements": AdapterQuiescenceRequirement("vanilla_advancements", True, False, False, None, "Player offline"),
    "vanilla_stats": AdapterQuiescenceRequirement("vanilla_stats", True, False, False, None, "Player offline"),
    "auraskills": AdapterQuiescenceRequirement("auraskills", True, False, False, None, "Player offline, flush on join"),
    "quests": AdapterQuiescenceRequirement("quests", True, False, False, None, "Player offline"),
    "multiverse_inventories": AdapterQuiescenceRequirement("multiverse_inventories", True, False, True, "mvinv reload", "Cache em memória requer flush"),
    "imageframe": AdapterQuiescenceRequirement("imageframe", True, False, False, None, "Player offline"),
    "huskhomes": AdapterQuiescenceRequirement("huskhomes", True, False, False, None, "Player offline, SQLite journal=delete"),
    "elitemobs": AdapterQuiescenceRequirement("elitemobs", True, False, False, None, "Player offline, SQLite journal=delete"),
    "waypoints": AdapterQuiescenceRequirement("waypoints", True, False, False, None, "Player offline, SQLite journal=delete"),
    "ultimateteams": AdapterQuiescenceRequirement("ultimateteams", True, False, False, None, "Player offline, SQLite journal=wal"),
    "simplepets": AdapterQuiescenceRequirement("simplepets", True, False, False, None, "Player offline, SQLite journal=delete"),
    "marriagemaster": AdapterQuiescenceRequirement("marriagemaster", True, False, False, None, "Player offline, SQLite journal=delete"),
    "simplelogin": AdapterQuiescenceRequirement("simplelogin", False, False, False, None, "TRANSITION_PRESERVE: no-op"),
    "luckperms": AdapterQuiescenceRequirement("luckperms", False, True, False, "lp user clone", "Lock exclusivo H2: console ou offline"),
}
