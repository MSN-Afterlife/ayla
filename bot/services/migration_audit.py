import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


UTC = timezone.utc


class MigrationAuditStore:
    def __init__(self, database_path: str | Path, *, now=None) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = now or (lambda: datetime.now(UTC))
        self._apply_schema()

    def record(
        self,
        event_type: str,
        *,
        discord_user_id: str | int | None = None,
        canonical_uuid: str | None = None,
        migration_id: str | None = None,
        presence: str | None = None,
        lock_state: str | None = None,
        result: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO minecraft_migration_audit_events(
                    event_type, discord_user_id, canonical_uuid, migration_id,
                    presence, lock_state, result, error, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    str(discord_user_id) if discord_user_id is not None else None,
                    canonical_uuid,
                    migration_id,
                    presence,
                    lock_state,
                    result,
                    _sanitize(error),
                    json.dumps(_sanitize_metadata(metadata or {}), ensure_ascii=False, sort_keys=True),
                    self._clock().astimezone(UTC).isoformat(timespec="seconds"),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10, isolation_level=None)

    def _apply_schema(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS minecraft_migration_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    discord_user_id TEXT,
                    canonical_uuid TEXT,
                    migration_id TEXT,
                    presence TEXT,
                    lock_state TEXT,
                    result TEXT,
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )


def _sanitize(error: object) -> str | None:
    if error is None:
        return None
    text = str(error).replace("\n", " ").replace("\r", " ").strip()
    return text[:500]


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    blocked = {"token", "authorization", "secret", "password", "api_key"}
    result = {}
    for key, value in metadata.items():
        if any(part in str(key).casefold() for part in blocked):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
        else:
            result[str(key)] = str(value)[:500]
    return result
