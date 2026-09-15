from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ValidationSeverity(StrEnum):
    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class LegacyProfile:
    user_id: Any
    balance: Any
    daily_streak: Any
    last_daily_at: Any
    updated_at: Any
    row_number: int


@dataclass(frozen=True)
class ProfileIssue:
    severity: ValidationSeverity
    code: str
    message: str
    user_id: Any = None
    row_number: int | None = None


@dataclass(frozen=True)
class LegacySnapshot:
    snapshot_id: str
    source: str
    created_at: str
    schema_version: str
    schema_fingerprint: str
    content_hash: str
    profiles: tuple[LegacyProfile, ...]
    content_hash_algorithm: str = "AYLA_LEGACY_CONTENT_HASH_V1"
    schema_fingerprint_algorithm: str = "AYLA_LEGACY_SCHEMA_FINGERPRINT_V1"


@dataclass(frozen=True)
class LegacyValidationReport:
    snapshot_id: str
    issues: tuple[ProfileIssue, ...]
    valid_profiles: tuple[LegacyProfile, ...]
    median: float
    p95: int
    p99: int
    maximum: int

    @property
    def warnings(self) -> tuple[ProfileIssue, ...]:
        return tuple(i for i in self.issues if i.severity == ValidationSeverity.WARNING)

    @property
    def blockers(self) -> tuple[ProfileIssue, ...]:
        return tuple(i for i in self.issues if i.severity == ValidationSeverity.BLOCKED)

    @property
    def valid_count(self) -> int:
        return len(self.valid_profiles)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def blocked_count(self) -> int:
        return len(self.blockers)

    @property
    def legacy_total(self) -> int:
        return sum(int(p.balance) for p in self.valid_profiles)
