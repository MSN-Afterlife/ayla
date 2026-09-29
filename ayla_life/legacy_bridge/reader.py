from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from pathlib import Path
from statistics import median
from typing import Any

from .canonical import legacy_content_hash, legacy_schema_fingerprint
from .models import LegacyProfile, LegacySnapshot, LegacyValidationReport, ProfileIssue, ValidationSeverity


class LegacyReadError(RuntimeError):
    """The legacy source could not be inspected without changing it."""


class LegacySchemaError(LegacyReadError):
    pass


REQUIRED_COLUMNS = ("user_id", "balance", "daily_streak", "last_daily_at", "updated_at")
SCHEMA_VERSION = "economy_profiles:v1"
ABSURD_BALANCE = 10**15


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * fraction))
    return ordered[rank - 1]


class LegacyEconomyReader:
    """Read-only reader for a legacy SQLite database.

    Every connection uses SQLite's URI read-only mode. No schema creation,
    pragma writes, migration, or transaction write is performed here.
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.exists():
            raise LegacyReadError(f"legacy database does not exist: {self.database_path}")
        uri = self.database_path.as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.Error as exc:
            raise LegacyReadError(f"cannot open legacy database read-only: {exc}") from exc
        connection.row_factory = sqlite3.Row
        return connection

    def read_snapshot(self) -> LegacySnapshot:
        with closing(self._connect()) as db:
            table = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='economy_profiles'").fetchone()
            if not table:
                raise LegacySchemaError("required table economy_profiles is missing")
            columns = db.execute("PRAGMA table_info(economy_profiles)").fetchall()
            names = tuple(row[1] for row in columns)
            missing = [name for name in REQUIRED_COLUMNS if name not in names]
            if missing:
                raise LegacySchemaError(f"economy_profiles is missing columns: {', '.join(missing)}")
            schema = [{"cid": row[0], "name": row[1], "type": row[2], "notnull": row[3], "default": row[4], "pk": row[5]} for row in columns]
            schema_fingerprint = legacy_schema_fingerprint(schema)
            rows = db.execute("SELECT user_id,balance,daily_streak,last_daily_at,updated_at FROM economy_profiles").fetchall()
            profiles = tuple(LegacyProfile(*(row[name] for name in REQUIRED_COLUMNS), row_number=index) for index, row in enumerate(rows, 1))
        canonical = [{name: getattr(profile, name) for name in REQUIRED_COLUMNS} for profile in profiles]
        content_hash = legacy_content_hash(canonical)
        snapshot_id = f"legacy-{content_hash[:24]}"
        from ayla_life.economy.repository import utc_now
        return LegacySnapshot(snapshot_id, str(self.database_path), utc_now(), SCHEMA_VERSION, schema_fingerprint, content_hash, profiles)

    def validate(self, snapshot: LegacySnapshot) -> LegacyValidationReport:
        issues: list[ProfileIssue] = []
        valid: list[LegacyProfile] = []
        id_counts: dict[int, int] = {}
        for candidate in snapshot.profiles:
            if isinstance(candidate.user_id, int) and not isinstance(candidate.user_id, bool):
                id_counts[candidate.user_id] = id_counts.get(candidate.user_id, 0) + 1
        for profile in snapshot.profiles:
            blocked: list[ProfileIssue] = []
            if not isinstance(profile.user_id, int) or isinstance(profile.user_id, bool) or profile.user_id <= 0:
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "INVALID_USER_ID", "user_id must be a positive integer", profile.user_id, profile.row_number))
            if not isinstance(profile.balance, int) or isinstance(profile.balance, bool):
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "INVALID_BALANCE", "balance must be an integer", profile.user_id, profile.row_number))
            elif profile.balance < 0:
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "NEGATIVE_BALANCE", "negative balances are not imported", profile.user_id, profile.row_number))
            elif profile.balance > ABSURD_BALANCE:
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "ABSURD_BALANCE", "balance exceeds safety ceiling", profile.user_id, profile.row_number))
            for field in ("daily_streak", "updated_at"):
                value = getattr(profile, field)
                if not isinstance(value, int) or isinstance(value, bool):
                    blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "INVALID_FIELD", f"{field} must be an integer", profile.user_id, profile.row_number))
            if profile.last_daily_at is not None and (not isinstance(profile.last_daily_at, int) or isinstance(profile.last_daily_at, bool)):
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "INVALID_FIELD", "last_daily_at must be an integer or NULL", profile.user_id, profile.row_number))
            if isinstance(profile.daily_streak, int) and profile.daily_streak < 0:
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "NEGATIVE_DAILY_STREAK", "daily_streak cannot be negative", profile.user_id, profile.row_number))
            if isinstance(profile.user_id, int) and not isinstance(profile.user_id, bool) and id_counts.get(profile.user_id, 0) > 1:
                blocked.append(ProfileIssue(ValidationSeverity.BLOCKED, "DUPLICATE_USER_ID", "user_id occurs more than once", profile.user_id, profile.row_number))
            if blocked:
                issues.extend(blocked)
            elif isinstance(profile.user_id, int):
                valid.append(profile)
        balances = [int(profile.balance) for profile in valid]
        med = float(median(balances)) if balances else 0.0
        p95, p99, maximum = _percentile(balances, .95), _percentile(balances, .99), max(balances, default=0)
        warning_cutoff = max(1_000_000, med * 2)
        for profile in valid:
            if profile.balance >= warning_cutoff and profile.balance > 1_000_000:
                issues.append(ProfileIssue(ValidationSeverity.WARNING, "BALANCE_OUTLIER", "balance is a statistical outlier; review before cutover", profile.user_id, profile.row_number))
        return LegacyValidationReport(snapshot.snapshot_id, tuple(issues), tuple(valid), med, p95, p99, maximum)
