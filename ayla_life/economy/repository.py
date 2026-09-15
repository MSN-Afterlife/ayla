from __future__ import annotations

import json
import sqlite3
from threading import RLock
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS currencies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    emoji TEXT,
    decimal_places INTEGER NOT NULL CHECK(decimal_places BETWEEN 0 AND 9),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','RETIRED','PARALLEL','FAILED')),
    created_at TEXT NOT NULL,
    retired_at TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    account_type TEXT NOT NULL CHECK(account_type IN ('PLAYER','CENTRAL_BANK','TREASURY','HOUSE','ESCROW','NPC','BUSINESS','SYSTEM')),
    owner_type TEXT,
    owner_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','FROZEN','CLOSED')),
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_transactions (
    id TEXT PRIMARY KEY,
    transaction_type TEXT NOT NULL CHECK(transaction_type IN ('TRANSFER','MONETARY_ISSUANCE','MONETARY_BURN','ADMIN_ADJUSTMENT','GENESIS','REVERSAL')),
    status TEXT NOT NULL CHECK(status IN ('PENDING','POSTED','REVERSED','FAILED')),
    actor_type TEXT,
    actor_id TEXT,
    reference_type TEXT,
    reference_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_hash TEXT NOT NULL,
    description TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    posted_at TEXT,
    reversal_of TEXT UNIQUE REFERENCES ledger_transactions(id)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL REFERENCES ledger_transactions(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    currency_id TEXT NOT NULL REFERENCES currencies(id),
    amount INTEGER NOT NULL CHECK(amount <> 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_account_currency ON ledger_entries(account_id, currency_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_transaction ON ledger_entries(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_type_status ON ledger_transactions(transaction_type, status);

CREATE TABLE IF NOT EXISTS account_balances (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    currency_id TEXT NOT NULL REFERENCES currencies(id),
    balance INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account_id, currency_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor_type TEXT,
    actor_id TEXT,
    target_type TEXT,
    target_id TEXT,
    transaction_id TEXT REFERENCES ledger_transactions(id),
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_transaction ON audit_events(transaction_id);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_events(target_type, target_id);

DROP TRIGGER IF EXISTS prevent_posted_transaction_delete;
DROP TRIGGER IF EXISTS prevent_posted_transaction_update;
DROP TRIGGER IF EXISTS prevent_posted_entry_update;
DROP TRIGGER IF EXISTS prevent_posted_entry_delete;

CREATE TRIGGER prevent_posted_transaction_delete
BEFORE DELETE ON ledger_transactions
WHEN OLD.status = 'POSTED'
BEGIN SELECT RAISE(ABORT, 'posted transactions are immutable'); END;

CREATE TRIGGER prevent_posted_transaction_update
BEFORE UPDATE ON ledger_transactions
WHEN OLD.status = 'POSTED'
BEGIN SELECT RAISE(ABORT, 'posted transactions are immutable'); END;

CREATE TRIGGER prevent_posted_entry_update
BEFORE UPDATE ON ledger_entries
WHEN (SELECT status FROM ledger_transactions WHERE id = OLD.transaction_id) = 'POSTED'
BEGIN SELECT RAISE(ABORT, 'entries of posted transactions are immutable'); END;

CREATE TRIGGER prevent_posted_entry_delete
BEFORE DELETE ON ledger_entries
WHEN (SELECT status FROM ledger_transactions WHERE id = OLD.transaction_id) = 'POSTED'
BEGIN SELECT RAISE(ABORT, 'entries of posted transactions are immutable'); END;
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def encode_json(value: dict) -> str:
    if not isinstance(value, dict):
        raise ValueError("metadata must be a JSON object")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must contain JSON-safe values") from exc


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
    finally:
        connection.close()


def initialize(path: str | Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)


class SQLiteRepository:
    """Owns one SQLite connection for a kernel lifecycle.

    SQLite writes are serialized by the process lock. Database transactions
    remain explicit, durable, and restart-safe; the lock only avoids needless
    connection churn and same-process races.
    """

    def __init__(self, path: str | Path) -> None:
        database = Path(path)
        database.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database, timeout=5, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(SCHEMA)

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
