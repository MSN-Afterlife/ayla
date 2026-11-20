"""Persistent moderation cases, warnings, and guild log configuration."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


UTC = timezone.utc
MAX_TIMEOUT_SECONDS = 28 * 24 * 3600  # Discord limit: 28 days
_DURATION_TOKEN_RE = re.compile(r"(\d+)\s*(s|seg|m|min|h|hr|d|dia|dias|w|sem)", re.IGNORECASE)
_UNIT_SECONDS = {
    "s": 1,
    "seg": 1,
    "m": 60,
    "min": 60,
    "h": 3600,
    "hr": 3600,
    "d": 86400,
    "dia": 86400,
    "dias": 86400,
    "w": 604800,
    "sem": 604800,
}


@dataclass(frozen=True)
class ModerationCase:
    id: int
    guild_id: int
    user_id: int
    moderator_id: int
    action: str
    reason: str
    duration_seconds: int | None
    active: bool
    created_at: str


def parse_duration(raw: str) -> timedelta:
    """Parse durations like '10m', '2h30m', '7d' into a timedelta (max 28 days)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Informe uma duracao valida (ex: `10m`, `1h`, `7d`).")

    if text.isdigit():
        total_seconds = int(text) * 60
    else:
        matches = list(_DURATION_TOKEN_RE.finditer(text))
        consumed = "".join(m.group(0) for m in matches).replace(" ", "")
        if not matches or consumed.lower() != text.replace(" ", "").lower():
            raise ValueError("Duracao invalida. Use formatos como `30s`, `10m`, `2h`, `7d` ou `1h30m`.")
        total_seconds = sum(int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()] for m in matches)

    if total_seconds <= 0:
        raise ValueError("A duracao precisa ser maior que zero.")
    if total_seconds > MAX_TIMEOUT_SECONDS:
        raise ValueError("A duracao maxima para silenciamento no Discord e de 28 dias.")
    return timedelta(seconds=total_seconds)


def format_duration(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return "permanente"
    remaining = int(seconds)
    parts: list[str] = []
    for unit_seconds, label in ((86400, "d"), (3600, "h"), (60, "m"), (1, "s")):
        if remaining >= unit_seconds:
            value, remaining = divmod(remaining, unit_seconds)
            parts.append(f"{value}{label}")
    return " ".join(parts) or "0s"


class ModerationService:
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
            migration = Path(__file__).resolve().parents[2] / "migrations" / "005_moderation.sql"
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                ("005_moderation", self._clock().astimezone(UTC).isoformat(timespec="seconds")),
            )

    def record_case(
        self,
        *,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        duration_seconds: int | None = None,
    ) -> ModerationCase:
        now = self._clock().astimezone(UTC).isoformat(timespec="seconds")
        clean_reason = (reason or "Sem motivo informado").strip()[:500]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO moderation_cases(
                    guild_id, user_id, moderator_id, action, reason, duration_seconds, active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (int(guild_id), int(user_id), int(moderator_id), action, clean_reason, duration_seconds, now),
            )
            case_id = int(cursor.lastrowid)
            connection.commit()
        return ModerationCase(
            id=case_id,
            guild_id=int(guild_id),
            user_id=int(user_id),
            moderator_id=int(moderator_id),
            action=action,
            reason=clean_reason,
            duration_seconds=duration_seconds,
            active=True,
            created_at=now,
        )

    def list_user_cases(
        self,
        guild_id: int,
        user_id: int,
        *,
        active_warns_only: bool = False,
        limit: int = 15,
    ) -> list[ModerationCase]:
        query = """
            SELECT id, guild_id, user_id, moderator_id, action, reason, duration_seconds, active, created_at
            FROM moderation_cases
            WHERE guild_id = ? AND user_id = ?
        """
        params: list[int] = [int(guild_id), int(user_id)]
        if active_warns_only:
            query += " AND action = 'warn' AND active = 1"
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 50)))

        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            ModerationCase(
                id=int(row["id"]),
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                moderator_id=int(row["moderator_id"]),
                action=str(row["action"]),
                reason=str(row["reason"]),
                duration_seconds=row["duration_seconds"],
                active=bool(row["active"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def count_active_warns(self, guild_id: int, user_id: int) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM moderation_cases WHERE guild_id = ? AND user_id = ? AND action = 'warn' AND active = 1",
                (int(guild_id), int(user_id)),
            ).fetchone()
        return int(row["total"]) if row else 0

    def deactivate_warn(self, guild_id: int, case_id: int) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE moderation_cases SET active = 0 WHERE guild_id = ? AND id = ? AND action = 'warn' AND active = 1",
                (int(guild_id), int(case_id)),
            )
            connection.commit()
            return bool(cursor.rowcount)

    def clear_user_warns(self, guild_id: int, user_id: int) -> int:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE moderation_cases SET active = 0 WHERE guild_id = ? AND user_id = ? AND action = 'warn' AND active = 1",
                (int(guild_id), int(user_id)),
            )
            connection.commit()
            return int(cursor.rowcount)

    def get_log_channel_id(self, guild_id: int) -> int | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT log_channel_id FROM moderation_guild_config WHERE guild_id = ?",
                (int(guild_id),),
            ).fetchone()
        if not row or row["log_channel_id"] is None:
            return None
        return int(row["log_channel_id"])

    def set_log_channel_id(self, guild_id: int, channel_id: int) -> None:
        now = self._clock().astimezone(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO moderation_guild_config(guild_id, log_channel_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    log_channel_id = excluded.log_channel_id,
                    updated_at = excluded.updated_at
                """,
                (int(guild_id), int(channel_id), now),
            )
            connection.commit()

    def clear_log_channel_id(self, guild_id: int) -> None:
        now = self._clock().astimezone(UTC).isoformat(timespec="seconds")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO moderation_guild_config(guild_id, log_channel_id, updated_at)
                VALUES (?, NULL, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    log_channel_id = NULL,
                    updated_at = excluded.updated_at
                """,
                (int(guild_id), now),
            )
            connection.commit()
