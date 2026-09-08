import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)
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
        try:
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
        except Exception:
            logger.exception("Failed to write migration audit event event_type=%s migration_id=%s", event_type, migration_id)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path, timeout=10, isolation_level=None)

    def _apply_schema(self) -> None:
        with closing(self._connect()) as connection:
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='minecraft_migration_audit_events'"
            ).fetchone()
            if not existing:
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
                connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_discord ON minecraft_migration_audit_events(discord_user_id)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_canonical ON minecraft_migration_audit_events(canonical_uuid)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_migration ON minecraft_migration_audit_events(migration_id)")
                self._record_schema_migration(connection)
                return

            cols = {row[1] for row in connection.execute("PRAGMA table_info(minecraft_migration_audit_events)").fetchall()}
            required_cols = {
                "event_type",
                "discord_user_id",
                "canonical_uuid",
                "migration_id",
                "presence",
                "lock_state",
                "result",
                "error",
                "metadata_json",
                "created_at",
            }
            if not required_cols.issubset(cols):
                logger.warning("Migrating legacy minecraft_migration_audit_events schema...")
                connection.execute("BEGIN IMMEDIATE TRANSACTION")
                try:
                    connection.execute(
                        """
                        CREATE TABLE _migration_audit_events_rebuild (
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
                    user_expr = "user_id" if "user_id" in cols else ("discord_user_id" if "discord_user_id" in cols else "NULL")
                    canon_expr = "canonical_uuid" if "canonical_uuid" in cols else "NULL"
                    mig_expr = "migration_id" if "migration_id" in cols else "NULL"
                    pres_expr = "presence" if "presence" in cols else "NULL"
                    lock_expr = "lock_state" if "lock_state" in cols else "NULL"
                    res_expr = "result" if "result" in cols else "NULL"
                    err_expr = "details" if "details" in cols else ("error" if "error" in cols else "NULL")
                    meta_expr = "metadata_json" if "metadata_json" in cols else "'{}'"
                    time_expr = "timestamp" if "timestamp" in cols else ("created_at" if "created_at" in cols else "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")

                    connection.execute(
                        f"""
                        INSERT INTO _migration_audit_events_rebuild (
                            id, event_type, discord_user_id, canonical_uuid, migration_id,
                            presence, lock_state, result, error, metadata_json, created_at
                        )
                        SELECT
                            id,
                            event_type,
                            {user_expr},
                            {canon_expr},
                            {mig_expr},
                            {pres_expr},
                            {lock_expr},
                            {res_expr},
                            {err_expr},
                            {meta_expr},
                            coalesce({time_expr}, '')
                        FROM minecraft_migration_audit_events
                        """
                    )
                    connection.execute("DROP TABLE minecraft_migration_audit_events")
                    connection.execute("ALTER TABLE _migration_audit_events_rebuild RENAME TO minecraft_migration_audit_events")
                    connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_discord ON minecraft_migration_audit_events(discord_user_id)")
                    connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_canonical ON minecraft_migration_audit_events(canonical_uuid)")
                    connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_migration ON minecraft_migration_audit_events(migration_id)")
                    self._record_schema_migration(connection)
                    connection.commit()
                    logger.info("Successfully migrated minecraft_migration_audit_events schema")
                except Exception:
                    connection.rollback()
                    raise
            else:
                connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_discord ON minecraft_migration_audit_events(discord_user_id)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_canonical ON minecraft_migration_audit_events(canonical_uuid)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_migration_audit_events_migration ON minecraft_migration_audit_events(migration_id)")
                self._record_schema_migration(connection)

    def _record_schema_migration(self, connection: sqlite3.Connection) -> None:
        has_migrations = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if has_migrations:
            already = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version='003_minecraft_migration_audit'"
            ).fetchone()
            if not already:
                now_str = self._clock().astimezone(UTC).isoformat(timespec="seconds")
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES ('003_minecraft_migration_audit', ?)",
                    (now_str,),
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
