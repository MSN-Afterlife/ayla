import random
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from bot.config import Settings


@dataclass(frozen=True)
class LevelProfile:
    user_id: int
    user_name: str
    xp: int
    level: int
    current_level_xp: int
    next_level_xp: int
    rank: int


class LevelService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_path = Path(settings.levels_database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add_message_xp(self, guild_id: int, user_id: int, user_name: str) -> tuple[LevelProfile, LevelProfile] | None:
        now = int(time.time())

        with closing(self._connect()) as connection:
            cooldown = connection.execute(
                "SELECT last_xp_at FROM xp_cooldowns WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()

            if cooldown and now - int(cooldown["last_xp_at"]) < self._settings.levels_cooldown_seconds:
                return None

            xp_gain = random.randint(self._settings.levels_xp_min, self._settings.levels_xp_max)
            connection.execute(
                """
                INSERT INTO xp_cooldowns (guild_id, user_id, last_xp_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET last_xp_at = excluded.last_xp_at
                """,
                (guild_id, user_id, now),
            )
            self._add_xp(connection, "global_levels", user_id, None, user_name, xp_gain)
            self._add_xp(connection, "guild_levels", user_id, guild_id, user_name, xp_gain)
            connection.commit()

        return (
            self.get_global_profile(user_id, user_name),
            self.get_guild_profile(guild_id, user_id, user_name),
        )

    def get_global_profile(self, user_id: int, user_name: str) -> LevelProfile:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, user_name, xp FROM global_levels WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            rank = self._rank(connection, "global_levels", user_id)

        return self._profile_from_row(row, user_id, user_name, rank)

    def get_guild_profile(self, guild_id: int, user_id: int, user_name: str) -> LevelProfile:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, user_name, xp FROM guild_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            rank = self._rank(connection, "guild_levels", user_id, guild_id)

        return self._profile_from_row(row, user_id, user_name, rank)

    def get_global_leaderboard(self, limit: int = 10) -> list[LevelProfile]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT user_id, user_name, xp FROM global_levels ORDER BY xp DESC, user_id ASC LIMIT ?",
                (limit,),
            ).fetchall()

        return [self._profile_from_row(row, row["user_id"], row["user_name"], index + 1) for index, row in enumerate(rows)]

    def get_guild_leaderboard(self, guild_id: int, limit: int = 10) -> list[LevelProfile]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT user_id, user_name, xp
                FROM guild_levels
                WHERE guild_id = ?
                ORDER BY xp DESC, user_id ASC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()

        return [self._profile_from_row(row, row["user_id"], row["user_name"], index + 1) for index, row in enumerate(rows)]

    def get_profile_background(self, user_id: int) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT background_url FROM profile_backgrounds WHERE user_id = ?",
                (user_id,),
            ).fetchone()

        return row["background_url"] if row else None

    def set_profile_background(self, user_id: int, background_url: str) -> None:
        now = int(time.time())
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO profile_backgrounds (user_id, background_url, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    background_url = excluded.background_url,
                    updated_at = excluded.updated_at
                """,
                (user_id, background_url, now),
            )
            connection.commit()

    def clear_profile_background(self, user_id: int) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM profile_backgrounds WHERE user_id = ?", (user_id,))
            connection.commit()

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS global_levels (
                    user_id INTEGER PRIMARY KEY,
                    user_name TEXT NOT NULL,
                    xp INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_levels (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    xp INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS xp_cooldowns (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    last_xp_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_backgrounds (
                    user_id INTEGER PRIMARY KEY,
                    background_url TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _add_xp(
        self,
        connection: sqlite3.Connection,
        table: str,
        user_id: int,
        guild_id: int | None,
        user_name: str,
        xp_gain: int,
    ) -> None:
        now = int(time.time())

        if table == "global_levels":
            connection.execute(
                """
                INSERT INTO global_levels (user_id, user_name, xp, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    user_name = excluded.user_name,
                    xp = global_levels.xp + excluded.xp,
                    updated_at = excluded.updated_at
                """,
                (user_id, user_name, xp_gain, now),
            )
            return

        connection.execute(
            """
            INSERT INTO guild_levels (guild_id, user_id, user_name, xp, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                user_name = excluded.user_name,
                xp = guild_levels.xp + excluded.xp,
                updated_at = excluded.updated_at
            """,
            (guild_id, user_id, user_name, xp_gain, now),
        )

    def _rank(self, connection: sqlite3.Connection, table: str, user_id: int, guild_id: int | None = None) -> int:
        if table == "global_levels":
            row = connection.execute(
                """
                SELECT COUNT(*) + 1 AS rank
                FROM global_levels
                WHERE xp > COALESCE((SELECT xp FROM global_levels WHERE user_id = ?), 0)
                """,
                (user_id,),
            ).fetchone()
            return int(row["rank"])

        row = connection.execute(
            """
            SELECT COUNT(*) + 1 AS rank
            FROM guild_levels
            WHERE guild_id = ?
              AND xp > COALESCE((SELECT xp FROM guild_levels WHERE guild_id = ? AND user_id = ?), 0)
            """,
            (guild_id, guild_id, user_id),
        ).fetchone()
        return int(row["rank"])

    def _profile_from_row(self, row, user_id: int, user_name: str, rank: int) -> LevelProfile:
        xp = int(row["xp"]) if row else 0
        level = level_from_xp(xp)
        current_level_xp = xp_for_level(level)
        next_level_xp = xp_for_level(level + 1)

        return LevelProfile(
            user_id=user_id,
            user_name=row["user_name"] if row else user_name,
            xp=xp,
            level=level,
            current_level_xp=current_level_xp,
            next_level_xp=next_level_xp,
            rank=rank,
        )


def xp_for_level(level: int) -> int:
    return 100 * level * level


def level_from_xp(xp: int) -> int:
    level = 0
    while xp >= xp_for_level(level + 1):
        level += 1
    return level
