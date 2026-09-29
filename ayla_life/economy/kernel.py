from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import (
    Account,
    AccountStatus,
    AccountType,
    Currency,
    CurrencyStatus,
    LedgerEntry,
    LedgerStatus,
    LedgerTransaction,
    MoneySupply,
    ReconciliationDifference,
    ReconciliationReport,
    TransactionType,
)
from .repository import SQLiteRepository, encode_json, new_id, utc_now


class EconomicKernel:
    """Discord-independent, double-entry economic kernel.

    Money uses integer minor units. Every posted transaction has balanced
    entries per currency, and the account_balances read model is maintained
    in the same SQLite transaction as the ledger entries.
    """

    def __init__(self, database_path: str | Path, *, failure_hook: Callable[[str], None] | None = None) -> None:
        self.database_path = Path(database_path)
        self._repository = SQLiteRepository(self.database_path)
        self._failure_hook = failure_hook

    def close(self) -> None:
        self._repository.close()

    def __enter__(self) -> "EconomicKernel":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def create_currency(self, name: str, code: str, *, emoji: str | None = None,
                        decimal_places: int = 0, status: CurrencyStatus = CurrencyStatus.ACTIVE,
                        metadata: dict[str, Any] | None = None, currency_id: str | None = None) -> Currency:
        try:
            status = CurrencyStatus(status)
        except ValueError as exc:
            raise ValueError("invalid currency status") from exc
        if not name.strip() or not code.strip():
            raise ValueError("currency name and code are required")
        code = code.strip().upper()
        if len(code) > 32:
            raise ValueError("currency code is too long")
        if not 0 <= decimal_places <= 9:
            raise ValueError("decimal_places must be between 0 and 9")
        now = utc_now(); currency_id = currency_id or new_id("cur")
        try:
            with self._repository.session() as db:
                db.execute("INSERT INTO currencies VALUES (?,?,?,?,?,?,?,?,?)", (currency_id, name.strip(), code, emoji, decimal_places, status.value, now, None, encode_json(metadata or {})))
        except sqlite3.IntegrityError as exc:
            raise ValueError("currency id or code already exists") from exc
        return self.get_currency(currency_id)

    def get_currency(self, currency_id: str) -> Currency:
        with self._repository.session() as db:
            row = db.execute("SELECT * FROM currencies WHERE id=?", (currency_id,)).fetchone()
        if not row: raise KeyError(f"unknown currency: {currency_id}")
        return Currency(row["id"], row["name"], row["code"], row["emoji"], row["decimal_places"], CurrencyStatus(row["status"]), row["created_at"], row["retired_at"], json.loads(row["metadata"]))

    def create_account(self, account_type: AccountType, *, owner_type: str | None = None,
                       owner_id: str | None = None, status: AccountStatus = AccountStatus.ACTIVE,
                       metadata: dict[str, Any] | None = None, account_id: str | None = None) -> Account:
        try:
            account_type = AccountType(account_type)
            status = AccountStatus(status)
        except ValueError as exc:
            raise ValueError("invalid account type or status") from exc
        if (owner_type is None) != (owner_id is None) or owner_type == "" or owner_id == "":
            raise ValueError("owner_type and owner_id must be supplied together")
        if not account_id or not account_id.strip():
            raise ValueError("account_id is required")
        now = utc_now(); account_id = account_id or new_id("acct")
        try:
            with self._repository.session() as db:
                db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", (account_id, account_type.value, owner_type, owner_id, status.value, encode_json(metadata or {}), now, now))
        except sqlite3.IntegrityError as exc:
            raise ValueError("account id already exists") from exc
        return self.get_account(account_id)

    def get_account(self, account_id: str) -> Account:
        with self._repository.session() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row: raise KeyError(f"unknown account: {account_id}")
        return Account(row["id"], AccountType(row["account_type"]), row["owner_type"], row["owner_id"], AccountStatus(row["status"]), json.loads(row["metadata"]), row["created_at"], row["updated_at"])

    def set_account_status(self, account_id: str, status: AccountStatus) -> Account:
        try:
            status = AccountStatus(status)
        except ValueError as exc:
            raise ValueError("invalid account status") from exc
        now = utc_now()
        with self._repository.session() as db:
            if not db.execute("SELECT 1 FROM accounts WHERE id=?", (account_id,)).fetchone(): raise KeyError(account_id)
            db.execute("UPDATE accounts SET status=?, updated_at=? WHERE id=?", (status.value, now, account_id))
        return self.get_account(account_id)

    def transfer(self, from_account: str, to_account: str, currency: str, amount: int,
                 idempotency_key: str, *, actor_type: str | None = None, actor_id: str | None = None,
                 reference_type: str | None = None, reference_id: str | None = None,
                 description: str | None = None, metadata: dict[str, Any] | None = None) -> LedgerTransaction:
        self._require_positive_amount(amount)
        if from_account == to_account: raise ValueError("source and destination must differ")
        return self._post(TransactionType.TRANSFER, [(from_account, currency, -amount), (to_account, currency, amount)], idempotency_key, actor_type=actor_type, actor_id=actor_id, reference_type=reference_type, reference_id=reference_id, description=description, metadata=metadata)

    def issue(self, to_account: str, currency: str, amount: int, idempotency_key: str,
              *, authority_account: str, actor_type: str | None = None, actor_id: str | None = None,
              description: str | None = None, metadata: dict[str, Any] | None = None) -> LedgerTransaction:
        self._require_positive_amount(amount)
        issuance = f"system:issuance:{currency}"
        return self._post(TransactionType.MONETARY_ISSUANCE, [(issuance, currency, -amount), (to_account, currency, amount)], idempotency_key, actor_type=actor_type or "CENTRAL_BANK", actor_id=actor_id or authority_account, description=description, metadata=metadata, technical_accounts=((issuance, "issuance", currency),), authority_account=authority_account)

    def burn(self, from_account: str, currency: str, amount: int, idempotency_key: str,
             *, authority_account: str, actor_type: str | None = None, actor_id: str | None = None,
             description: str | None = None, metadata: dict[str, Any] | None = None) -> LedgerTransaction:
        self._require_positive_amount(amount)
        burn = f"system:burn:{currency}"
        return self._post(TransactionType.MONETARY_BURN, [(from_account, currency, -amount), (burn, currency, amount)], idempotency_key, actor_type=actor_type or "CENTRAL_BANK", actor_id=actor_id or authority_account, description=description, metadata=metadata, technical_accounts=((burn, "burn", currency),), authority_account=authority_account)

    def genesis(self, to_account: str, currency: str, amount: int, idempotency_key: str,
                *, authority_account: str, description: str | None = None,
                metadata: dict[str, Any] | None = None) -> LedgerTransaction:
        self._require_positive_amount(amount)
        source = f"system:genesis:{currency}"
        return self._post(TransactionType.GENESIS, [(source, currency, -amount), (to_account, currency, amount)], idempotency_key, actor_type="GENESIS", actor_id=authority_account, description=description, metadata=metadata, technical_accounts=((source, "genesis", currency),), authority_account=authority_account)

    def reverse(self, transaction_id: str, idempotency_key: str, *, actor_type: str | None = None,
                actor_id: str | None = None, description: str | None = None,
                metadata: dict[str, Any] | None = None) -> LedgerTransaction:
        with self._repository.session() as db:
            original = db.execute("SELECT * FROM ledger_transactions WHERE id=?", (transaction_id,)).fetchone()
            if not original: raise KeyError(transaction_id)
            if original["status"] != LedgerStatus.POSTED.value: raise ValueError("only POSTED transactions may be reversed")
            if original["transaction_type"] == TransactionType.REVERSAL.value: raise ValueError("a reversal cannot be reversed")
            entries = db.execute("SELECT account_id,currency_id,amount FROM ledger_entries WHERE transaction_id=?", (transaction_id,)).fetchall()
        return self._post(TransactionType.REVERSAL, [(e["account_id"], e["currency_id"], -e["amount"]) for e in entries], idempotency_key, actor_type=actor_type, actor_id=actor_id, reference_type="LEDGER_TRANSACTION", reference_id=transaction_id, description=description or f"Reversal of {transaction_id}", metadata=metadata, reversal_of=transaction_id)

    def get_balance(self, account_id: str, currency_id: str) -> int:
        with self._repository.session() as db:
            row = db.execute("SELECT balance FROM account_balances WHERE account_id=? AND currency_id=?", (account_id, currency_id)).fetchone()
        return int(row["balance"]) if row else 0

    def get_transaction(self, transaction_id: str) -> LedgerTransaction:
        with self._repository.session() as db:
            row = db.execute("SELECT * FROM ledger_transactions WHERE id=?", (transaction_id,)).fetchone()
        if not row: raise KeyError(transaction_id)
        return self._transaction(row)

    def money_supply(self, currency_id: str) -> MoneySupply:
        with self._repository.session() as db:
            values = {}
            for kind in (TransactionType.MONETARY_ISSUANCE, TransactionType.MONETARY_BURN, TransactionType.GENESIS):
                row = db.execute("""
                    SELECT COALESCE(SUM(le.amount), 0) AS total
                    FROM ledger_entries le
                    JOIN ledger_transactions lt ON lt.id = le.transaction_id
                    LEFT JOIN ledger_transactions original ON original.id = lt.reversal_of
                    JOIN accounts a ON a.id = le.account_id
                    WHERE lt.status = 'POSTED'
                      AND le.currency_id = ?
                      AND a.account_type <> 'SYSTEM'
                      AND (lt.transaction_type = ? OR (lt.transaction_type = 'REVERSAL' AND original.transaction_type = ?))
                """, (currency_id, kind.value, kind.value)).fetchone()
                total = int(row["total"])
                values[kind] = -total if kind == TransactionType.MONETARY_BURN else total
        return MoneySupply(currency_id, values[TransactionType.MONETARY_ISSUANCE], values[TransactionType.MONETARY_BURN], values[TransactionType.GENESIS])

    def reconcile(self) -> ReconciliationReport:
        with self._repository.session() as db:
            ledger = {(r["account_id"], r["currency_id"]): int(r["balance"]) for r in db.execute("SELECT le.account_id, le.currency_id, COALESCE(SUM(le.amount),0) balance FROM ledger_entries le JOIN ledger_transactions lt ON lt.id=le.transaction_id WHERE lt.status='POSTED' GROUP BY le.account_id, le.currency_id")}
            cached = {(r["account_id"], r["currency_id"]): int(r["balance"]) for r in db.execute("SELECT account_id,currency_id,balance FROM account_balances")}
            accounts = {key[0] for key in ledger | cached}; currencies = {key[1] for key in ledger | cached}
            differences = tuple(ReconciliationDifference(a, c, ledger.get((a,c),0), cached.get((a,c),0), ledger.get((a,c),0)-cached.get((a,c),0)) for a,c in sorted(ledger.keys() | cached.keys()) if ledger.get((a,c),0) != cached.get((a,c),0))
        return ReconciliationReport(len(accounts), len(currencies), len(accounts) * len(currencies) - len(differences), differences)

    def _post(self, transaction_type: TransactionType, entries: list[tuple[str, str, int]], idempotency_key: str, *, actor_type: str | None, actor_id: str | None, reference_type: str | None = None, reference_id: str | None = None, description: str | None = None, metadata: dict[str, Any] | None = None, reversal_of: str | None = None, technical_accounts: tuple[tuple[str, str, str], ...] = (), authority_account: str | None = None) -> LedgerTransaction:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip(): raise ValueError("idempotency_key is required")
        if len(idempotency_key) > 255: raise ValueError("idempotency_key is too long")
        if not entries: raise ValueError("transaction needs entries")
        if any(not isinstance(amount, int) or isinstance(amount, bool) for _, _, amount in entries): raise ValueError("entry amounts must be integers")
        if any(amount == 0 for _, _, amount in entries): raise ValueError("zero entries are not allowed")
        by_currency: dict[str, int] = defaultdict(int)
        for _, currency, amount in entries: by_currency[currency] += amount
        if any(total != 0 for total in by_currency.values()): raise ValueError("entries must balance per currency")
        payload = {"type": transaction_type.value, "entries": sorted(entries), "actor_type": actor_type, "actor_id": actor_id, "reference_type": reference_type, "reference_id": reference_id, "description": description, "metadata": metadata or {}, "reversal_of": reversal_of}
        payload_hash = hashlib.sha256(encode_json(payload).encode()).hexdigest()
        now = utc_now(); transaction_id = new_id("tx")
        with self._repository.transaction() as db:
            existing = db.execute("SELECT * FROM ledger_transactions WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash: raise ValueError("idempotency key already used with a different payload")
                return self._transaction(existing)
            if authority_account:
                authority = db.execute("SELECT account_type,status FROM accounts WHERE id=?", (authority_account,)).fetchone()
                if not authority or authority["account_type"] != AccountType.CENTRAL_BANK.value or authority["status"] != AccountStatus.ACTIVE.value:
                    raise PermissionError("only an active CENTRAL_BANK account may authorize this operation")
            if reversal_of and db.execute("SELECT 1 FROM ledger_transactions WHERE reversal_of=?", (reversal_of,)).fetchone():
                raise ValueError("transaction already has a reversal")
            for technical_id, role, currency_id in technical_accounts:
                self._ensure_technical_account(db, technical_id, role, currency_id)
            self._validate_entries(db, entries, transaction_type)
            db.execute("INSERT INTO ledger_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (transaction_id, transaction_type.value, LedgerStatus.POSTED.value, actor_type, actor_id, reference_type, reference_id, idempotency_key, payload_hash, description, encode_json(metadata or {}), now, now, reversal_of))
            self._fail("after_transaction_insert")
            for account_id, currency_id, amount in entries:
                db.execute("INSERT INTO ledger_entries(transaction_id,account_id,currency_id,amount,created_at) VALUES (?,?,?,?,?)", (transaction_id, account_id, currency_id, amount, now))
                self._fail("after_ledger_entry")
                db.execute("INSERT INTO account_balances(account_id,currency_id,balance,version,updated_at) VALUES (?,?,?,1,?) ON CONFLICT(account_id,currency_id) DO UPDATE SET balance=account_balances.balance+excluded.balance, version=account_balances.version+1, updated_at=excluded.updated_at", (account_id, currency_id, amount, now))
                self._fail("after_balance_update")
            self._fail("before_audit")
            db.execute("INSERT INTO audit_events(event_type,actor_type,actor_id,target_type,target_id,transaction_id,metadata,created_at) VALUES (?,?,?,?,?,?,?,?)", (self._audit_event(transaction_type), actor_type, actor_id, "LEDGER_TRANSACTION", transaction_id, transaction_id, encode_json({"idempotency_key": idempotency_key}), now))
            self._fail("before_commit")
        return self.get_transaction(transaction_id)

    def _validate_entries(self, db: sqlite3.Connection, entries: Iterable[tuple[str, str, int]], transaction_type: TransactionType) -> None:
        deltas: dict[tuple[str, str], int] = defaultdict(int)
        for account_id, currency_id, amount in entries:
            account = db.execute("SELECT account_type,status FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not account: raise KeyError(f"unknown account: {account_id}")
            currency = db.execute("SELECT status FROM currencies WHERE id=?", (currency_id,)).fetchone()
            if not currency: raise KeyError(f"unknown currency: {currency_id}")
            if currency["status"] == CurrencyStatus.FAILED.value: raise ValueError("FAILED currency cannot be used")
            if currency["status"] == CurrencyStatus.RETIRED.value and transaction_type != TransactionType.REVERSAL: raise ValueError("RETIRED currency cannot be used for new transactions")
            if account["status"] == AccountStatus.CLOSED.value: raise ValueError("closed account cannot transact")
            if account["account_type"] == AccountType.SYSTEM.value and transaction_type not in {TransactionType.MONETARY_ISSUANCE, TransactionType.MONETARY_BURN, TransactionType.GENESIS, TransactionType.REVERSAL}: raise ValueError("SYSTEM accounts cannot use ordinary transactions")
            if amount < 0 and account["status"] == AccountStatus.FROZEN.value and account["account_type"] not in {AccountType.SYSTEM.value, AccountType.CENTRAL_BANK.value}: raise ValueError("frozen account cannot originate funds")
            deltas[(account_id, currency_id)] += amount
        for (account_id, currency_id), delta in deltas.items():
            row = db.execute("SELECT account_type FROM accounts WHERE id=?", (account_id,)).fetchone()
            if row["account_type"] == AccountType.SYSTEM.value: continue
            balance_row = db.execute("SELECT balance FROM account_balances WHERE account_id=? AND currency_id=?", (account_id, currency_id)).fetchone()
            balance = balance_row["balance"] if balance_row else 0
            if balance + delta < 0: raise ValueError("insufficient funds")

    @staticmethod
    def _require_positive_amount(amount: int) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("amount must be a positive integer")

    def _fail(self, stage: str) -> None:
        if self._failure_hook:
            self._failure_hook(stage)

    @staticmethod
    def _ensure_technical_account(db: sqlite3.Connection, account_id: str, role: str, currency_id: str) -> None:
        row = db.execute("SELECT id FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            now = utc_now()
            db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", (account_id, AccountType.SYSTEM.value, "MONETARY_SYSTEM", role, AccountStatus.ACTIVE.value, encode_json({"currency_id": currency_id, "role": role}), now, now))

    @staticmethod
    def _audit_event(transaction_type: TransactionType) -> str:
        return {TransactionType.MONETARY_ISSUANCE: "MONEY_ISSUED", TransactionType.MONETARY_BURN: "MONEY_BURNED", TransactionType.REVERSAL: "TRANSACTION_REVERSED", TransactionType.TRANSFER: "TRANSFER_POSTED", TransactionType.GENESIS: "GENESIS_POSTED"}.get(transaction_type, "TRANSACTION_POSTED")

    @staticmethod
    def _transaction(row: sqlite3.Row) -> LedgerTransaction:
        return LedgerTransaction(row["id"], TransactionType(row["transaction_type"]), LedgerStatus(row["status"]), row["actor_type"], row["actor_id"], row["reference_type"], row["reference_id"], row["idempotency_key"], row["description"], json.loads(row["metadata"]), row["created_at"], row["posted_at"], row["reversal_of"])
