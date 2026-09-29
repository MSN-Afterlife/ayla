"""Persistent, per-guild authorization for Ayla's staging operations."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


UTC = timezone.utc


class StagingRbacStore:
    def __init__(self, database_path: str | Path, *, now=None) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = now or (lambda: datetime.now(UTC))
        self._apply_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _apply_schema(self) -> None:
        with closing(self._connect()) as connection:
            migration = Path(__file__).resolve().parents[2] / "migrations" / "004_staging_rbac.sql"
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                ("004_staging_rbac", self._clock().astimezone(UTC).isoformat(timespec="seconds")),
            )

    def role_ids(self, guild_id: int) -> set[int]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT role_id FROM staging_authorized_roles WHERE guild_id=?",
                (int(guild_id),),
            ).fetchall()
        return {int(row["role_id"]) for row in rows}

    def add_role(self, guild_id: int, role_id: int, created_by: int) -> bool:
        now = self._clock().astimezone(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "INSERT OR IGNORE INTO staging_authorized_roles(guild_id, role_id, created_at, created_by) VALUES (?, ?, ?, ?)",
                (int(guild_id), int(role_id), now, int(created_by)),
            )
            if cursor.rowcount:
                connection.execute(
                    "INSERT INTO staging_rbac_audit_events(guild_id, operator_user_id, action, role_id, timestamp) VALUES (?, ?, 'ADMIN_ROLE_ADDED', ?, ?)",
                    (int(guild_id), int(created_by), int(role_id), now),
                )
            connection.commit()
            return bool(cursor.rowcount)

    def remove_role(self, guild_id: int, role_id: int, operator_user_id: int) -> bool:
        now = self._clock().astimezone(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM staging_authorized_roles WHERE guild_id=? AND role_id=?",
                (int(guild_id), int(role_id)),
            )
            if cursor.rowcount:
                connection.execute(
                    "INSERT INTO staging_rbac_audit_events(guild_id, operator_user_id, action, role_id, timestamp) VALUES (?, ?, 'ADMIN_ROLE_REMOVED', ?, ?)",
                    (int(guild_id), int(operator_user_id), int(role_id), now),
                )
            connection.commit()
            return bool(cursor.rowcount)


def is_discord_administrator(member) -> bool:
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and getattr(permissions, "administrator", False))


def is_staging_operator(member, guild_id: int, store: StagingRbacStore) -> bool:
    """Return whether *member* is authorized in this guild, with live role data."""
    if not member or not guild_id:
        return False
    member_guild = getattr(member, "guild", None)
    if member_guild is not None and int(getattr(member_guild, "id", 0)) != int(guild_id):
        return False
    if is_discord_administrator(member):
        return True
    configured = store.role_ids(int(guild_id))
    member_role_ids = {int(getattr(role, "id", 0)) for role in (getattr(member, "roles", None) or ())}
    return bool(configured & member_role_ids)


def can_use_admin_command(subject, *, required: str = "administrator") -> bool:
    """Apply staging RBAC while preserving the command's production permission."""
    bot = getattr(subject, "bot", None) or getattr(subject, "client", None)
    settings = getattr(bot, "_settings", None)
    if getattr(settings, "environment", "production") == "staging":
        guild = getattr(subject, "guild", None)
        store = getattr(bot, "_staging_rbac", None)
        if guild and store and is_staging_operator(getattr(subject, "author", None) or getattr(subject, "user", None), guild.id, store):
            return True

    member = getattr(subject, "author", None) or getattr(subject, "user", None) or subject
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and getattr(permissions, required, False))


def staging_app_command_check(required: str = "administrator"):
    async def predicate(interaction) -> bool:
        return can_use_admin_command(interaction, required=required)

    return predicate


def staging_prefix_check(required: str = "administrator"):
    async def predicate(ctx) -> bool:
        return can_use_admin_command(ctx, required=required)

    return predicate
