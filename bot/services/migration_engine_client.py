import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp


class PresenceState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE_CONFIRMED = "OFFLINE_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class LockState(str, Enum):
    ABSENT = "ABSENT"
    UNLOCKED = "UNLOCKED"
    LOCKING = "LOCKING"
    MIGRATING = "MIGRATING"
    ROLLING_BACK = "ROLLING_BACK"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CRITICAL_FAILURE = "CRITICAL_FAILURE"
    UNKNOWN = "UNKNOWN"

    @property
    def blocks_mutation(self) -> bool:
        return self in {
            LockState.LOCKING,
            LockState.MIGRATING,
            LockState.ROLLING_BACK,
            LockState.RECOVERY_REQUIRED,
            LockState.CRITICAL_FAILURE,
            LockState.UNKNOWN,
        }


class MigrationState(str, Enum):
    INSPECTED = "INSPECTED"
    PLANNED = "PLANNED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNKNOWN = "UNKNOWN"


class MigrationErrorCode(str, Enum):
    CONFLICT = "conflict"
    PLAYER_ONLINE = "player_online"
    PRESENCE_UNKNOWN = "presence_unknown"
    LOCK_ACTIVE = "lock_active"
    NOT_FOUND = "not_found"
    ALREADY_EXECUTED = "already_executed"
    ROLLBACK_UNAVAILABLE = "rollback_unavailable"
    AUTH_FAILED = "auth_failed"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    NOT_CONFIGURED = "not_configured"


class MigrationEngineError(Exception):
    code = MigrationErrorCode.UNAVAILABLE

    def __init__(self, message: str, *, status: int | None = None, payload: object | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class MigrationEngineNotConfigured(MigrationEngineError):
    code = MigrationErrorCode.NOT_CONFIGURED


class MigrationEngineAuthError(MigrationEngineError):
    code = MigrationErrorCode.AUTH_FAILED


class MigrationEngineUnavailable(MigrationEngineError):
    code = MigrationErrorCode.UNAVAILABLE


class MigrationEngineInvalidResponse(MigrationEngineError):
    code = MigrationErrorCode.INVALID_RESPONSE


class MigrationEngineRejected(MigrationEngineError):
    def __init__(self, code: MigrationErrorCode, message: str, *, status: int | None = None, payload: object | None = None) -> None:
        super().__init__(message, status=status, payload=payload)
        self.code = code


@dataclass(frozen=True)
class MinecraftAccountRef:
    platform: str
    external_id: str
    username: str | None = None


@dataclass(frozen=True)
class IdentitySummary:
    canonical_uuid: str
    discord_user_id: str | None = None
    canonical_name: str | None = None
    java: MinecraftAccountRef | None = None
    bedrock: MinecraftAccountRef | None = None
    source_uuids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationInspection:
    identity: IdentitySummary
    presence: PresenceState
    lock_state: LockState
    detected_data: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannedOperation:
    dataset: str
    action: str
    count: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class MigrationPlan:
    migration_id: str
    source_identities: tuple[str, ...]
    canonical_target: str
    operations: tuple[PlannedOperation, ...] = ()
    affected_files: tuple[str, ...] = ()
    affected_datasets: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    rollback_available: bool = False
    presence: PresenceState = PresenceState.UNKNOWN
    lock_state: LockState = LockState.UNKNOWN

    def allows_mutation(self) -> bool:
        return self.presence is PresenceState.OFFLINE_CONFIRMED and not self.blockers and not self.lock_state.blocks_mutation


@dataclass(frozen=True)
class MigrationExecution:
    migration_id: str
    result: str
    migrated_datasets: tuple[str, ...] = ()
    verifications: tuple[str, ...] = ()
    snapshot_ref: str | None = None
    rollback_available: bool = False
    presence: PresenceState = PresenceState.UNKNOWN
    lock_state: LockState = LockState.UNKNOWN


@dataclass(frozen=True)
class MigrationRollback:
    migration_id: str
    result: str
    restored_data: tuple[str, ...] = ()
    verifications: tuple[str, ...] = ()
    presence: PresenceState = PresenceState.UNKNOWN
    lock_state: LockState = LockState.UNKNOWN


@dataclass(frozen=True)
class MigrationStatus:
    migration_id: str
    state: MigrationState
    created_at: str | None = None
    updated_at: str | None = None
    presence: PresenceState = PresenceState.UNKNOWN
    lock_state: LockState = LockState.UNKNOWN
    verification_state: str | None = None
    rollback_available: bool = False
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def allows_execute_confirmation(self) -> bool:
        return self.presence is PresenceState.OFFLINE_CONFIRMED and not self.blockers and not self.lock_state.blocks_mutation


@dataclass(frozen=True)
class MigrationInspectRequest:
    discord_user_id: str | None = None
    canonical_uuid: str | None = None
    platform: str | None = None
    external_id: str | None = None

    def payload(self) -> dict[str, str]:
        result = {
            "discord_user_id": self.discord_user_id,
            "canonical_uuid": self.canonical_uuid,
            "platform": self.platform,
            "external_id": self.external_id,
        }
        return {key: value for key, value in result.items() if value}


@dataclass(frozen=True)
class MigrationPlanRequest(MigrationInspectRequest):
    reason: str | None = None

    def payload(self) -> dict[str, str]:
        result = super().payload()
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class MigrationEngineClient:
    base_url: str | None
    token: str | None
    timeout_seconds: float = 10
    session_factory: Any = aiohttp.ClientSession
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    async def inspect(self, request: MigrationInspectRequest) -> MigrationInspection:
        payload = await self._request("POST", "/api/v1/migrations/inspect", json_payload=request.payload())
        return _parse_inspection(payload)

    async def plan(self, request: MigrationPlanRequest) -> MigrationPlan:
        payload = await self._request("POST", "/api/v1/migrations/plan", json_payload=request.payload())
        return _parse_plan(payload)

    async def execute(self, migration_id: str, *, operator_id: str, idempotency_key: str | None = None) -> MigrationExecution:
        payload = await self._request(
            "POST",
            f"/api/v1/migrations/{_path_id(migration_id)}/execute",
            json_payload={"operator_id": operator_id},
            idempotency_key=idempotency_key,
        )
        return _parse_execution(payload)

    async def rollback(self, migration_id: str, *, operator_id: str, idempotency_key: str | None = None) -> MigrationRollback:
        payload = await self._request(
            "POST",
            f"/api/v1/migrations/{_path_id(migration_id)}/rollback",
            json_payload={"operator_id": operator_id},
            idempotency_key=idempotency_key,
        )
        return _parse_rollback(payload)

    async def status(self, migration_id: str) -> MigrationStatus:
        payload = await self._request("GET", f"/api/v1/migrations/{_path_id(migration_id)}")
        return _parse_status(payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise MigrationEngineNotConfigured("Migration Engine nao configurado.")
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json", **self.extra_headers}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{str(self.base_url).rstrip('/')}{path}"
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with self.session_factory(timeout=timeout) as session:
                async with session.request(method, url, json=json_payload, headers=headers) as response:
                    payload = await _read_json(response)
                    if response.status in {401, 403}:
                        raise MigrationEngineAuthError("Migration Engine recusou autenticacao.", status=response.status, payload=payload)
                    if response.status >= 400:
                        raise _rejected_from_response(response.status, payload)
                    if not isinstance(payload, dict):
                        raise MigrationEngineInvalidResponse("Resposta do Migration Engine nao e um objeto JSON.", status=response.status, payload=payload)
                    return payload
        except MigrationEngineError:
            raise
        except (TimeoutError, asyncio.TimeoutError):
            raise MigrationEngineUnavailable("Timeout ao consultar Migration Engine.") from None
        except (aiohttp.ClientError, OSError) as error:
            raise MigrationEngineUnavailable(f"Migration Engine indisponivel: {type(error).__name__}.") from None


async def _read_json(response) -> object:
    try:
        return await response.json()
    except Exception as error:
        raise MigrationEngineInvalidResponse("Migration Engine retornou JSON invalido.", status=getattr(response, "status", None)) from error


def _rejected_from_response(status: int, payload: object) -> MigrationEngineRejected:
    code_value = _field(payload, "code", "error_code", default="conflict")
    try:
        code = MigrationErrorCode(str(code_value))
    except ValueError:
        code = MigrationErrorCode.CONFLICT
    message = str(_field(payload, "message", "error", default="Migration Engine recusou a operacao."))
    return MigrationEngineRejected(code, message, status=status, payload=payload)


def _parse_inspection(payload: object) -> MigrationInspection:
    data = _require_dict(payload)
    return MigrationInspection(
        identity=_parse_identity(_require_dict(data.get("identity"))),
        presence=_parse_enum(PresenceState, data.get("presence"), "presence"),
        lock_state=_parse_enum(LockState, data.get("lock_state") or data.get("lock"), "lock_state"),
        detected_data=_tuple_str(data.get("detected_data") or data.get("datasets")),
        warnings=_tuple_str(data.get("warnings")),
        blockers=_tuple_str(data.get("blockers")),
    )


def _parse_plan(payload: object) -> MigrationPlan:
    data = _require_dict(payload)
    operations = tuple(_parse_operation(item) for item in _list(data.get("operations")))
    migration_id = _required_str(data, "migration_id")
    return MigrationPlan(
        migration_id=migration_id,
        source_identities=_tuple_str(data.get("source_identities")),
        canonical_target=_required_str(data, "canonical_target"),
        operations=operations,
        affected_files=_tuple_str(data.get("affected_files")),
        affected_datasets=_tuple_str(data.get("affected_datasets")),
        conflicts=_tuple_str(data.get("conflicts")),
        warnings=_tuple_str(data.get("warnings")),
        blockers=_tuple_str(data.get("blockers")),
        rollback_available=bool(data.get("rollback_available")),
        presence=_parse_enum(PresenceState, data.get("presence"), "presence"),
        lock_state=_parse_enum(LockState, data.get("lock_state") or data.get("lock"), "lock_state"),
    )


def _parse_execution(payload: object) -> MigrationExecution:
    data = _require_dict(payload)
    return MigrationExecution(
        migration_id=_required_str(data, "migration_id"),
        result=_required_str(data, "result"),
        migrated_datasets=_tuple_str(data.get("migrated_datasets") or data.get("datasets")),
        verifications=_tuple_str(data.get("verifications")),
        snapshot_ref=_optional_str(data.get("snapshot_ref") or data.get("snapshot")),
        rollback_available=bool(data.get("rollback_available")),
        presence=_parse_enum(PresenceState, data.get("presence", PresenceState.UNKNOWN.value), "presence"),
        lock_state=_parse_enum(LockState, data.get("lock_state") or data.get("lock") or LockState.UNKNOWN.value, "lock_state"),
    )


def _parse_rollback(payload: object) -> MigrationRollback:
    data = _require_dict(payload)
    return MigrationRollback(
        migration_id=_required_str(data, "migration_id"),
        result=_required_str(data, "result"),
        restored_data=_tuple_str(data.get("restored_data") or data.get("datasets")),
        verifications=_tuple_str(data.get("verifications")),
        presence=_parse_enum(PresenceState, data.get("presence", PresenceState.UNKNOWN.value), "presence"),
        lock_state=_parse_enum(LockState, data.get("lock_state") or data.get("lock") or LockState.UNKNOWN.value, "lock_state"),
    )


def _parse_status(payload: object) -> MigrationStatus:
    data = _require_dict(payload)
    return MigrationStatus(
        migration_id=_required_str(data, "migration_id"),
        state=_parse_enum(MigrationState, data.get("state"), "state"),
        created_at=_optional_str(data.get("created_at")),
        updated_at=_optional_str(data.get("updated_at")),
        presence=_parse_enum(PresenceState, data.get("presence"), "presence"),
        lock_state=_parse_enum(LockState, data.get("lock_state") or data.get("lock"), "lock_state"),
        verification_state=_optional_str(data.get("verification_state")),
        rollback_available=bool(data.get("rollback_available")),
        blockers=_tuple_str(data.get("blockers")),
        warnings=_tuple_str(data.get("warnings")),
    )


def _parse_identity(data: dict[str, Any]) -> IdentitySummary:
    return IdentitySummary(
        canonical_uuid=_required_str(data, "canonical_uuid"),
        discord_user_id=_optional_str(data.get("discord_user_id")),
        canonical_name=_optional_str(data.get("canonical_name")),
        java=_parse_account(data.get("java")),
        bedrock=_parse_account(data.get("bedrock")),
        source_uuids=_tuple_str(data.get("source_uuids")),
    )


def _parse_account(data: object) -> MinecraftAccountRef | None:
    if data is None:
        return None
    item = _require_dict(data)
    return MinecraftAccountRef(
        platform=_required_str(item, "platform"),
        external_id=_required_str(item, "external_id"),
        username=_optional_str(item.get("username") or item.get("current_username")),
    )


def _parse_operation(data: object) -> PlannedOperation:
    item = _require_dict(data)
    return PlannedOperation(
        dataset=_required_str(item, "dataset"),
        action=_required_str(item, "action"),
        count=_optional_int(item.get("count")),
        detail=_optional_str(item.get("detail")),
    )


def _parse_enum(enum_cls, value: object, field_name: str):
    try:
        return enum_cls(str(value))
    except ValueError:
        raise MigrationEngineInvalidResponse(f"{field_name} invalido na resposta do Migration Engine.") from None


def _require_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MigrationEngineInvalidResponse("Resposta do Migration Engine esta incompleta ou malformada.")
    return value


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MigrationEngineInvalidResponse(f"Campo obrigatorio ausente: {key}.")
    return value


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tuple_str(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _list(value) if str(item).strip())


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _field(payload: object, *keys: str, default: str) -> object:
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                return payload[key]
    return default


def _path_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or "/" in value or "\\" in value or ".." in value:
        raise ValueError("migration_id invalido.")
    return value
