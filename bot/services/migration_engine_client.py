import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlencode

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
        data = payload if isinstance(payload, dict) else {}
        self.blockers = tuple(str(item) for item in data.get("blockers", []) if str(item).strip())
        self.conflicts = tuple(str(item) for item in data.get("conflicts", []) if str(item).strip())


@dataclass(frozen=True)
class MinecraftAccountRef:
    platform: str
    external_id: str
    username: str | None = None


@dataclass(frozen=True)
class MigrationSource:
    platform: str
    external_id: str
    username: str | None = None
    physical_uuid: str | None = None

    def payload(self) -> dict[str, str]:
        result = {"platform": self.platform, "external_id": self.external_id}
        if self.username:
            result["username"] = self.username
        if self.physical_uuid:
            result["physical_uuid"] = self.physical_uuid
        return result


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
    status: str | None = None
    sources: tuple[MigrationSource, ...] = ()
    target_discord_user_id: str | None = None
    dataset_policy: dict[str, str] | None = None

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
class MigrationReference:
    migration_id: str
    state: MigrationState
    canonical_uuid: str | None = None
    player_name: str | None = None
    discord_user_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    rollback_available: bool = False

    def label(self) -> str:
        owner = self.player_name or self.canonical_uuid or self.discord_user_id or "migration"
        age = self.updated_at or self.created_at or "sem data"
        return f"{owner} | {self.state.value} | {age} | {self.migration_id[:18]}"


@dataclass(frozen=True)
class PlayerReference:
    canonical_uuid: str = ""
    player_name: str = ""
    discord_user_id: str | None = None
    java_external_id: str | None = None
    bedrock_external_id: str | None = None
    confidence: float | None = None
    platform: str | None = None
    external_id: str | None = None
    username: str | None = None
    sources: tuple[MigrationSource, ...] = ()

    def label(self) -> str:
        score = f" | {self.confidence:.0%}" if self.confidence is not None else ""
        identity = self.platform or (f"{len(self.sources)} sources" if self.sources else self.canonical_uuid[:8] or "identidade")
        return f"{self.player_name} | {identity}{score}"

    def source_list(self) -> tuple[MigrationSource, ...]:
        if self.sources:
            return self.sources
        if self.platform and self.external_id:
            return (MigrationSource(self.platform, self.external_id, self.username or self.player_name),)
        result = []
        if self.java_external_id:
            result.append(MigrationSource("java", self.java_external_id, self.player_name))
        if self.bedrock_external_id:
            result.append(MigrationSource("bedrock", self.bedrock_external_id, self.player_name))
        return tuple(result)


@dataclass(frozen=True)
class MigrationInspectRequest:
    discord_user_id: str | None = None
    canonical_uuid: str | None = None
    platform: str | None = None
    external_id: str | None = None
    player_name: str | None = None

    def payload(self) -> dict[str, str]:
        result = {
            "discord_user_id": self.discord_user_id,
            "canonical_uuid": self.canonical_uuid,
            "platform": self.platform,
            "external_id": self.external_id,
            "player_name": self.player_name,
        }
        return {key: value for key, value in result.items() if value}


@dataclass(frozen=True)
class MigrationPlanRequest(MigrationInspectRequest):
    reason: str | None = None
    sources: tuple[MigrationSource, ...] = ()
    target: dict[str, str] | None = None
    dataset_policy: dict[str, str] | None = None

    def payload(self) -> dict[str, Any]:
        if self.sources or self.target or self.dataset_policy:
            result: dict[str, Any] = {
                "sources": [source.payload() for source in self.sources],
                "target": dict(self.target or {}),
            }
            if self.dataset_policy:
                result["dataset_policy"] = dict(self.dataset_policy)
            if self.reason:
                result["reason"] = self.reason
            return result
        result = super().payload()
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class ItemEntry:
    id: str
    count: int = 1
    enchantments: tuple[str, ...] = ()
    slot: int | None = None


@dataclass(frozen=True)
class InventorySection:
    occupied_slots: int = 0
    total_items: int = 0
    items: tuple[ItemEntry, ...] = ()
    available: bool = True


@dataclass(frozen=True)
class EquipmentSummary:
    mainhand: ItemEntry | None = None
    offhand: ItemEntry | None = None
    head: ItemEntry | None = None
    chest: ItemEntry | None = None
    legs: ItemEntry | None = None
    feet: ItemEntry | None = None


@dataclass(frozen=True)
class PlayerdataPreview:
    available: bool = True
    error_message: str | None = None
    inventory: InventorySection = field(default_factory=InventorySection)
    ender_chest: InventorySection = field(default_factory=InventorySection)
    equipment: EquipmentSummary = field(default_factory=EquipmentSummary)
    xp_level: int | None = None
    xp_total: int | None = None
    xp_progress: float | None = None
    health: float | None = None
    food_level: int | None = None
    dimension: str | None = None
    position: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class AdvancementsPreview:
    available: bool = True
    error_message: str | None = None
    total_completed: int | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True)
class StatsPreview:
    available: bool = True
    error_message: str | None = None
    play_time_seconds: int | None = None
    deaths: int | None = None
    mob_kills: int | None = None
    player_kills: int | None = None
    blocks_mined: int | None = None
    distance_walked: int | None = None


@dataclass(frozen=True)
class SourceProfilePreview:
    platform: str
    external_id: str
    username: str | None = None
    playerdata: PlayerdataPreview = field(default_factory=PlayerdataPreview)
    advancements: AdvancementsPreview = field(default_factory=AdvancementsPreview)
    stats: StatsPreview = field(default_factory=StatsPreview)


@dataclass(frozen=True)
class MigrationPreviewRequest:
    sources: tuple[MigrationSource, ...] = ()
    target: dict[str, str] | None = None

    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "sources": [source.payload() for source in self.sources],
        }
        if self.target:
            result["target"] = dict(self.target)
        return result


@dataclass(frozen=True)
class MigrationPreviewResult:
    sources: tuple[SourceProfilePreview, ...] = ()
    warnings: tuple[str, ...] = ()


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

    async def preview(self, request: MigrationPreviewRequest) -> MigrationPreviewResult:
        payload = await self._request("POST", "/api/v1/migrations/preview", json_payload=request.payload())
        return _parse_preview_result(payload)

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

    async def list_migrations(
        self,
        *,
        query: str | None = None,
        selector: MigrationInspectRequest | None = None,
        state: MigrationState | None = None,
        rollback_available: bool | None = None,
        limit: int = 25,
    ) -> tuple[MigrationReference, ...]:
        params: dict[str, str] = {"limit": str(max(1, min(limit, 25)))}
        if query:
            params["query"] = query
        if selector:
            params.update(selector.payload())
        if state:
            params["state"] = state.value
        if rollback_available is not None:
            params["rollback_available"] = "true" if rollback_available else "false"
        payload = await self._request("GET", f"/api/v1/migrations?{urlencode(params)}")
        data = _require_dict(payload)
        migrations = data.get("migrations")
        if not isinstance(migrations, list):
            raise MigrationEngineInvalidResponse("Campo obrigatorio ausente: migrations.")
        return tuple(_parse_reference(item) for item in migrations)

    async def search_players(self, query: str, *, limit: int = 10) -> tuple[PlayerReference, ...]:
        params = urlencode({"query": query, "limit": str(max(1, min(limit, 25)))})
        payload = await self._request("GET", f"/api/v1/minecraft/players?{params}")
        data = _require_dict(payload)
        players = data.get("players")
        if not isinstance(players, list):
            raise MigrationEngineInvalidResponse("Campo obrigatorio ausente: players.")
        return tuple(_parse_player_reference(item) for item in players)

    async def staging_auth_bypass_status(self) -> bool:
        payload = await self._request("GET", "/api/v1/staging/auth-bypass")
        data = _require_dict(payload)
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise MigrationEngineInvalidResponse("Campo obrigatorio ausente: enabled.")
        return enabled

    async def set_staging_auth_bypass(self, enabled: bool) -> bool:
        payload = await self._request("POST", "/api/v1/staging/auth-bypass", json_payload={"enabled": enabled})
        data = _require_dict(payload)
        value = data.get("enabled")
        if not isinstance(value, bool):
            raise MigrationEngineInvalidResponse("Campo obrigatorio ausente: enabled.")
        return value

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
        canonical_target=_required_str(data, "canonical_target" if "canonical_target" in data else "canonical_uuid"),
        operations=operations,
        affected_files=_tuple_str(data.get("affected_files")),
        affected_datasets=_tuple_str(data.get("affected_datasets")),
        conflicts=_tuple_str(data.get("conflicts")),
        warnings=_tuple_str(data.get("warnings")),
        blockers=_tuple_str(data.get("blockers")),
        rollback_available=bool(data.get("rollback_available")),
        presence=_parse_enum(PresenceState, data.get("presence"), "presence"),
        lock_state=_parse_enum(LockState, data.get("lock_state") or data.get("lock"), "lock_state"),
        status=_optional_str(data.get("status") or data.get("state")),
        sources=tuple(_parse_source(item) for item in _list(data.get("sources"))),
        target_discord_user_id=_target_discord_id(data.get("target")) or _optional_str(data.get("target_discord_user_id")),
        dataset_policy=_parse_policy(data.get("dataset_policy")),
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


def _parse_reference(payload: object) -> MigrationReference:
    data = _require_dict(payload)
    return MigrationReference(
        migration_id=_required_str(data, "migration_id"),
        state=_parse_enum(MigrationState, data.get("state"), "state"),
        canonical_uuid=_optional_str(data.get("canonical_uuid") or data.get("canonical_target")),
        player_name=_optional_str(data.get("player_name") or data.get("canonical_name")),
        discord_user_id=_optional_str(data.get("discord_user_id")),
        created_at=_optional_str(data.get("created_at")),
        updated_at=_optional_str(data.get("updated_at")),
        rollback_available=bool(data.get("rollback_available")),
    )


def _parse_player_reference(payload: object) -> PlayerReference:
    data = _require_dict(payload)
    sources = tuple(_parse_source(item) for item in _list(data.get("sources")))
    platform = (_optional_str(data.get("platform")) or "").lower() or None
    external_id = _optional_str(data.get("external_id") or data.get("xuid"))
    return PlayerReference(
        canonical_uuid=_optional_str(data.get("canonical_uuid")) or "",
        player_name=_optional_str(data.get("player_name") or data.get("username")) or "",
        discord_user_id=_optional_str(data.get("discord_user_id")) or _target_discord_id(data.get("target")),
        java_external_id=_optional_str(data.get("java_external_id")),
        bedrock_external_id=_optional_str(data.get("bedrock_external_id")),
        confidence=_optional_float(data.get("confidence")),
        platform=platform,
        external_id=external_id,
        username=_optional_str(data.get("username")),
        sources=sources,
    )


def _parse_source(value: object) -> MigrationSource:
    data = _require_dict(value)
    platform = _required_str(data, "platform").lower()
    external_id = _required_str(data, "external_id" if "external_id" in data else "xuid")
    return MigrationSource(platform, external_id, _optional_str(data.get("username")), _optional_str(data.get("physical_uuid") or data.get("floodgate_uuid")))


def _target_discord_id(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    return _optional_str(value.get("discord_user_id"))


def _parse_policy(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items() if str(key).strip() and str(item).strip()}


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


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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


def _parse_preview_result(payload: object) -> MigrationPreviewResult:
    data = _require_dict(payload)
    sources_data = _list(data.get("sources"))
    sources = tuple(_parse_source_profile_preview(item) for item in sources_data)
    warnings = _tuple_str(data.get("warnings"))
    return MigrationPreviewResult(sources=sources, warnings=warnings)


def _parse_source_profile_preview(payload: object) -> SourceProfilePreview:
    data = _require_dict(payload)
    platform = _required_str(data, "platform").lower()
    external_id = _required_str(data, "external_id" if "external_id" in data else "xuid")
    username = _optional_str(data.get("username"))
    playerdata = _parse_playerdata_preview(data.get("playerdata"))
    advancements = _parse_advancements_preview(data.get("advancements"))
    stats = _parse_stats_preview(data.get("stats"))
    return SourceProfilePreview(
        platform=platform,
        external_id=external_id,
        username=username,
        playerdata=playerdata,
        advancements=advancements,
        stats=stats,
    )


def _parse_playerdata_preview(payload: object) -> PlayerdataPreview:
    if not isinstance(payload, dict):
        return PlayerdataPreview(available=False, error_message="indisponivel")
    available = bool(payload.get("available", True))
    error_message = _optional_str(payload.get("error_message") or payload.get("error"))
    inventory = _parse_inventory_section(payload.get("inventory"))
    ender_chest = _parse_inventory_section(payload.get("ender_chest"))
    equipment = _parse_equipment_summary(payload.get("equipment"))
    xp_level = _optional_int(payload.get("xp_level"))
    xp_total = _optional_int(payload.get("xp_total"))
    xp_progress = _optional_float(payload.get("xp_progress") or payload.get("xp_p"))
    health = _optional_float(payload.get("health"))
    food_level = _optional_int(payload.get("food_level"))
    dimension = _optional_str(payload.get("dimension"))
    position = _parse_position(payload.get("position") or payload.get("pos"))
    return PlayerdataPreview(
        available=available,
        error_message=error_message,
        inventory=inventory,
        ender_chest=ender_chest,
        equipment=equipment,
        xp_level=xp_level,
        xp_total=xp_total,
        xp_progress=xp_progress,
        health=health,
        food_level=food_level,
        dimension=dimension,
        position=position,
    )


def _parse_position(value: object) -> tuple[float, float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return (float(value[0]), float(value[1]), float(value[2]))
        except (ValueError, TypeError):
            return None
    return None


def _parse_inventory_section(payload: object) -> InventorySection:
    if not isinstance(payload, dict):
        return InventorySection(available=False)
    occupied_slots = _optional_int(payload.get("occupied_slots")) or 0
    total_items = _optional_int(payload.get("total_items")) or 0
    raw_items = _list(payload.get("items"))
    items = tuple(_parse_item_entry(item) for item in raw_items)
    available = bool(payload.get("available", True))
    return InventorySection(
        occupied_slots=occupied_slots,
        total_items=total_items,
        items=items,
        available=available,
    )


def _parse_item_entry(payload: object) -> ItemEntry:
    if not isinstance(payload, dict):
        return ItemEntry(id="unknown")
    item_id = str(payload.get("id") or "unknown")
    count = _optional_int(payload.get("count")) or 1
    enchantments = _tuple_str(payload.get("enchantments"))
    slot = _optional_int(payload.get("slot"))
    return ItemEntry(id=item_id, count=count, enchantments=enchantments, slot=slot)


def _parse_equipment_summary(payload: object) -> EquipmentSummary:
    if not isinstance(payload, dict):
        return EquipmentSummary()
    mainhand = _parse_item_entry(payload["mainhand"]) if isinstance(payload.get("mainhand"), dict) else None
    offhand = _parse_item_entry(payload["offhand"]) if isinstance(payload.get("offhand"), dict) else None
    armor_data = payload.get("armor") if isinstance(payload.get("armor"), dict) else payload
    head = _parse_item_entry(armor_data["head"]) if isinstance(armor_data.get("head"), dict) else None
    chest = _parse_item_entry(armor_data["chest"]) if isinstance(armor_data.get("chest"), dict) else None
    legs = _parse_item_entry(armor_data["legs"]) if isinstance(armor_data.get("legs"), dict) else None
    feet = _parse_item_entry(armor_data["feet"]) if isinstance(armor_data.get("feet"), dict) else None
    return EquipmentSummary(mainhand=mainhand, offhand=offhand, head=head, chest=chest, legs=legs, feet=feet)


def _parse_advancements_preview(payload: object) -> AdvancementsPreview:
    if not isinstance(payload, dict):
        return AdvancementsPreview(available=False, error_message="indisponivel")
    available = bool(payload.get("available", True))
    error_message = _optional_str(payload.get("error_message") or payload.get("error"))
    total_completed = _optional_int(payload.get("total_completed") or payload.get("completed"))
    highlights = _tuple_str(payload.get("highlights"))
    return AdvancementsPreview(
        available=available,
        error_message=error_message,
        total_completed=total_completed,
        highlights=highlights,
    )


def _parse_stats_preview(payload: object) -> StatsPreview:
    if not isinstance(payload, dict):
        return StatsPreview(available=False, error_message="indisponivel")
    available = bool(payload.get("available", True))
    error_message = _optional_str(payload.get("error_message") or payload.get("error"))
    play_time_seconds = _optional_int(payload.get("play_time_seconds") or payload.get("playtime_seconds"))
    deaths = _optional_int(payload.get("deaths"))
    mob_kills = _optional_int(payload.get("mob_kills"))
    player_kills = _optional_int(payload.get("player_kills"))
    blocks_mined = _optional_int(payload.get("blocks_mined"))
    distance_walked = _optional_int(payload.get("distance_walked"))
    return StatsPreview(
        available=available,
        error_message=error_message,
        play_time_seconds=play_time_seconds,
        deaths=deaths,
        mob_kills=mob_kills,
        player_kills=player_kills,
        blocks_mined=blocks_mined,
        distance_walked=distance_walked,
    )
