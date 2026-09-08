"""Persistência isolada da integração Last.fm.

O projeto não possui um cofre/criptografia de secrets. A session key fica
somente nesta base, fora dos logs; a limitação está documentada na documentação.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LastFmAccount:
    discord_user_id: int
    username: str
    session_key: str
    scrobble_enabled: bool
    scrobble_mode: str


class LastFmRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS lastfm_accounts (
                discord_user_id INTEGER PRIMARY KEY,
                lastfm_username TEXT NOT NULL,
                lastfm_session_key TEXT NOT NULL,
                scrobble_enabled INTEGER NOT NULL DEFAULT 1,
                scrobble_mode TEXT NOT NULL DEFAULT 'requested',
                linked_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lastfm_auth_states (
                state_hash TEXT PRIMARY KEY,
                discord_user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_lastfm_auth_states_expiry
                ON lastfm_auth_states(expires_at);
            """)

    def create_state(self, discord_user_id: int, ttl_seconds: int = 900) -> str:
        state = secrets.token_urlsafe(32)
        digest = hashlib.sha256(state.encode()).hexdigest()
        with self._connect() as db:
            db.execute("DELETE FROM lastfm_auth_states WHERE expires_at < ? OR used_at IS NOT NULL", (int(time.time()),))
            db.execute("INSERT INTO lastfm_auth_states VALUES (?, ?, ?, NULL)", (digest, discord_user_id, int(time.time()) + ttl_seconds))
        return state

    def consume_state(self, state: str) -> int | None:
        digest = hashlib.sha256(state.encode()).hexdigest()
        now = int(time.time())
        with self._connect() as db:
            row = db.execute(
                "UPDATE lastfm_auth_states SET used_at=? WHERE state_hash=? AND used_at IS NULL AND expires_at>=? RETURNING discord_user_id",
                (now, digest, now),
            ).fetchone()
            return int(row[0]) if row else None

    def save_account(self, discord_user_id: int, username: str, session_key: str) -> None:
        now = int(time.time())
        with self._connect() as db:
            db.execute("""INSERT INTO lastfm_accounts
                (discord_user_id,lastfm_username,lastfm_session_key,scrobble_enabled,scrobble_mode,linked_at,updated_at)
                VALUES (?, ?, ?, 1, 'requested', ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET lastfm_username=excluded.lastfm_username,
                lastfm_session_key=excluded.lastfm_session_key, scrobble_enabled=1, scrobble_mode='requested', updated_at=excluded.updated_at""",
                (discord_user_id, username, session_key, now, now))

    def get_account(self, discord_user_id: int) -> LastFmAccount | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM lastfm_accounts WHERE discord_user_id=?", (discord_user_id,)).fetchone()
        if not row:
            return None
        return LastFmAccount(int(row["discord_user_id"]), row["lastfm_username"], row["lastfm_session_key"], bool(row["scrobble_enabled"]), row["scrobble_mode"])

    def set_enabled(self, discord_user_id: int, enabled: bool) -> bool:
        with self._connect() as db:
            return db.execute("UPDATE lastfm_accounts SET scrobble_enabled=?, updated_at=? WHERE discord_user_id=?", (int(enabled), int(time.time()), discord_user_id)).rowcount > 0

    def disable(self, discord_user_id: int) -> None:
        self.set_enabled(discord_user_id, False)

    def disconnect(self, discord_user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM lastfm_accounts WHERE discord_user_id=?", (discord_user_id,))
            db.execute("DELETE FROM lastfm_auth_states WHERE discord_user_id=?", (discord_user_id,))

    def cancel_states(self, discord_user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM lastfm_auth_states WHERE discord_user_id=?", (discord_user_id,))
