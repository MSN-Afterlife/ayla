from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_uuid(val: str | None) -> str | None:
    if not val:
        return None
    cleaned = str(val).strip().lower()
    try:
        return str(uuid.UUID(cleaned))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"Formato de UUID inválido: {val}")


@dataclass
class MigrationIdentity:
    discord_user_id: str
    canonical_name: str
    canonical_uuid: str
    identity_id: int | None = None
    legacy_name: str | None = None
    legacy_uuid: str | None = None
    accounts: list[dict[str, Any]] = field(default_factory=list)
    migration_status: str = "PENDING"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.discord_user_id or not str(self.discord_user_id).strip():
            raise ValueError("discord_user_id é obrigatório.")
        if not self.canonical_name or not str(self.canonical_name).strip():
            raise ValueError("canonical_name é obrigatório.")
        self.canonical_uuid = normalize_uuid(self.canonical_uuid)
        if not self.canonical_uuid:
            raise ValueError("canonical_uuid é obrigatório.")
        if self.legacy_uuid:
            self.legacy_uuid = normalize_uuid(self.legacy_uuid)

    def java_account(self) -> dict[str, Any] | None:
        for acc in self.accounts:
            if str(acc.get("platform", "")).lower() == "java":
                return acc
        return None

    def bedrock_account(self) -> dict[str, Any] | None:
        for acc in self.accounts:
            if str(acc.get("platform", "")).lower() == "bedrock":
                return acc
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationIdentity:
        return cls(
            discord_user_id=str(data.get("discord_user_id", "")),
            identity_id=data.get("identity_id"),
            canonical_name=str(data.get("canonical_name", "")),
            canonical_uuid=str(data.get("canonical_uuid", "")),
            legacy_name=data.get("legacy_name"),
            legacy_uuid=data.get("legacy_uuid"),
            accounts=list(data.get("accounts", [])),
            migration_status=str(data.get("migration_status", "PENDING")),
        )


@dataclass
class MigrationDataFinding:
    adapter: str
    status: str  # FOUND, NOT_FOUND, LIMITED, SKIPPED
    source_found: bool
    target_found: bool
    source_records: int = 0
    target_records: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    conflict: bool = False
    warning: str | None = None
    blocker: str | None = None
    requires_player_offline: bool = True
    requires_paper_offline_for_write: bool = False
    migration_strategy: str = "RENAME_OR_MOVE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationDataFinding:
        return cls(**data)


@dataclass
class MigrationInspection:
    identity: MigrationIdentity
    server_root: str
    status: str = "READY"  # READY, BLOCKED, INCOMPLETE
    findings: list[MigrationDataFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    is_online: bool = False
    inspection_fingerprint: str = ""
    inspected_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        if self.blockers:
            self.status = "BLOCKED"
        elif not self.identity.legacy_uuid or not self.identity.canonical_uuid:
            self.status = "INCOMPLETE"
        elif not self.status:
            self.status = "READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity.to_dict(),
            "server_root": self.server_root,
            "status": self.status,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
            "blockers": self.blockers,
            "is_online": self.is_online,
            "inspection_fingerprint": self.inspection_fingerprint,
            "inspected_at": self.inspected_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationInspection:
        return cls(
            identity=MigrationIdentity.from_dict(data["identity"]),
            server_root=str(data.get("server_root", "")),
            status=str(data.get("status", "READY")),
            findings=[MigrationDataFinding.from_dict(f) for f in data.get("findings", [])],
            warnings=list(data.get("warnings", [])),
            blockers=list(data.get("blockers", [])),
            is_online=bool(data.get("is_online", False)),
            inspection_fingerprint=str(data.get("inspection_fingerprint", "")),
            inspected_at=str(data.get("inspected_at", now_iso())),
        )


@dataclass
class MigrationPlan:
    plan_id: str
    created_at: str
    identity: MigrationIdentity
    inspection_fingerprint: str
    status: str  # READY, BLOCKED, INCOMPLETE
    planned_changes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    approval_nonce: str = ""
    nonce_expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "identity": self.identity.to_dict(),
            "inspection_fingerprint": self.inspection_fingerprint,
            "status": self.status,
            "planned_changes": self.planned_changes,
            "warnings": self.warnings,
            "blockers": self.blockers,
            "summary": self.summary,
            "approval_nonce": self.approval_nonce,
            "nonce_expires_at": self.nonce_expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MigrationPlan:
        return cls(
            plan_id=str(data["plan_id"]),
            created_at=str(data.get("created_at", now_iso())),
            identity=MigrationIdentity.from_dict(data["identity"]),
            inspection_fingerprint=str(data.get("inspection_fingerprint", "")),
            status=str(data.get("status", "INCOMPLETE")),
            planned_changes=list(data.get("planned_changes", [])),
            warnings=list(data.get("warnings", [])),
            blockers=list(data.get("blockers", [])),
            summary=dict(data.get("summary", {})),
            approval_nonce=str(data.get("approval_nonce", "")),
            nonce_expires_at=float(data.get("nonce_expires_at", 0.0)),
        )
