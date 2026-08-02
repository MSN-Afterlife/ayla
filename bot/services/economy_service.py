import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from bot.config import Settings


DAILY_AMOUNT = 1500
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class EconomyProfile:
    user_id: int
    balance: int
    daily_streak: int
    last_daily_at: int | None


@dataclass(frozen=True)
class DailyClaim:
    profile: EconomyProfile
    remaining_seconds: int | None
    amount: int = 0
    bonus: int = 0


class EconomyService:
    def __init__(self, settings: Settings) -> None:
        self._database_path = Path(settings.levels_database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def get_profile(self, user_id: int) -> EconomyProfile:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, balance, daily_streak, last_daily_at FROM economy_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if not row:
                self._ensure_profile(connection, user_id)
                connection.commit()
                row = connection.execute(
                    "SELECT user_id, balance, daily_streak, last_daily_at FROM economy_profiles WHERE user_id = ?",
                    (user_id,),
                ).fetchone()

        return EconomyProfile(
            user_id=user_id,
            balance=int(row["balance"]),
            daily_streak=int(row["daily_streak"]),
            last_daily_at=int(row["last_daily_at"]) if row["last_daily_at"] is not None else None,
        )

    def claim_daily(self, user_id: int) -> DailyClaim:
        now = int(time.time())
        with closing(self._connect()) as connection:
            profile = self.get_profile(user_id)
            if profile.last_daily_at and now - profile.last_daily_at < DAILY_COOLDOWN_SECONDS:
                return DailyClaim(profile, DAILY_COOLDOWN_SECONDS - (now - profile.last_daily_at))

            streak = profile.daily_streak + 1 if profile.last_daily_at and now - profile.last_daily_at < DAILY_COOLDOWN_SECONDS * 2 else 1
            bonus = min(streak * 100, 1000)
            amount = DAILY_AMOUNT + bonus
            connection.execute(
                """
                UPDATE economy_profiles
                SET balance = balance + ?, daily_streak = ?, last_daily_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (amount, streak, now, now, user_id),
            )
            connection.commit()

        return DailyClaim(self.get_profile(user_id), None, amount, bonus)

    def transfer(self, sender_id: int, receiver_id: int, amount: int) -> tuple[EconomyProfile, EconomyProfile]:
        if amount <= 0:
            raise ValueError("O valor precisa ser maior que zero.")
        if sender_id == receiver_id:
            raise ValueError("Voce nao pode pagar a si mesmo.")

        now = int(time.time())
        with closing(self._connect()) as connection:
            self._ensure_profile(connection, sender_id)
            self._ensure_profile(connection, receiver_id)
            sender = connection.execute("SELECT balance FROM economy_profiles WHERE user_id = ?", (sender_id,)).fetchone()
            if int(sender["balance"]) < amount:
                raise ValueError("Saldo insuficiente.")

            connection.execute("UPDATE economy_profiles SET balance = balance - ?, updated_at = ? WHERE user_id = ?", (amount, now, sender_id))
            connection.execute("UPDATE economy_profiles SET balance = balance + ?, updated_at = ? WHERE user_id = ?", (amount, now, receiver_id))
            connection.commit()

        return self.get_profile(sender_id), self.get_profile(receiver_id)

    def add_balance(self, user_id: int, amount: int) -> EconomyProfile:
        now = int(time.time())
        with closing(self._connect()) as connection:
            self._ensure_profile(connection, user_id)
            row = connection.execute("SELECT balance FROM economy_profiles WHERE user_id = ?", (user_id,)).fetchone()
            new_balance = int(row["balance"]) + amount
            if new_balance < 0:
                raise ValueError("Saldo insuficiente.")
            connection.execute(
                "UPDATE economy_profiles SET balance = ?, updated_at = ? WHERE user_id = ?",
                (new_balance, now, user_id),
            )
            connection.commit()
        return self.get_profile(user_id)

    def can_afford(self, user_id: int, amount: int) -> bool:
        return self.get_profile(user_id).balance >= amount

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM economy_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        now = int(time.time())
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO economy_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            connection.commit()

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_profiles (
                    user_id INTEGER PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0,
                    daily_streak INTEGER NOT NULL DEFAULT 0,
                    last_daily_at INTEGER,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_profile(self, connection: sqlite3.Connection, user_id: int) -> None:
        now = int(time.time())
        connection.execute(
            """
            INSERT OR IGNORE INTO economy_profiles (user_id, balance, daily_streak, updated_at)
            VALUES (?, 0, 0, ?)
            """,
            (user_id, now),
        )
