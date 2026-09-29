from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CurrencyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"
    PARALLEL = "PARALLEL"
    FAILED = "FAILED"


class AccountType(StrEnum):
    PLAYER = "PLAYER"
    CENTRAL_BANK = "CENTRAL_BANK"
    TREASURY = "TREASURY"
    HOUSE = "HOUSE"
    ESCROW = "ESCROW"
    NPC = "NPC"
    BUSINESS = "BUSINESS"
    SYSTEM = "SYSTEM"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class TransactionType(StrEnum):
    TRANSFER = "TRANSFER"
    MONETARY_ISSUANCE = "MONETARY_ISSUANCE"
    MONETARY_BURN = "MONETARY_BURN"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"
    GENESIS = "GENESIS"
    REVERSAL = "REVERSAL"


class LedgerStatus(StrEnum):
    PENDING = "PENDING"
    POSTED = "POSTED"
    REVERSED = "REVERSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Currency:
    id: str
    name: str
    code: str
    emoji: str | None
    decimal_places: int
    status: CurrencyStatus
    created_at: str
    retired_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Account:
    id: str
    account_type: AccountType
    owner_type: str | None
    owner_id: str | None
    status: AccountStatus
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    transaction_id: str
    account_id: str
    currency_id: str
    amount: int
    created_at: str


@dataclass(frozen=True)
class LedgerTransaction:
    id: str
    transaction_type: TransactionType
    status: LedgerStatus
    actor_type: str | None
    actor_id: str | None
    reference_type: str | None
    reference_id: str | None
    idempotency_key: str
    description: str | None
    metadata: dict[str, Any]
    created_at: str
    posted_at: str | None
    reversal_of: str | None


@dataclass(frozen=True)
class ReconciliationDifference:
    account_id: str
    currency_id: str
    ledger_balance: int
    cached_balance: int
    difference: int


@dataclass(frozen=True)
class ReconciliationReport:
    accounts_checked: int
    currencies_checked: int
    matches: int
    differences: tuple[ReconciliationDifference, ...]

    @property
    def ok(self) -> bool:
        return not self.differences


@dataclass(frozen=True)
class MoneySupply:
    currency_id: str
    issued: int
    burned: int
    genesis: int

    @property
    def net_supply_created(self) -> int:
        return self.issued - self.burned

    @property
    def total_supply(self) -> int:
        return self.genesis + self.net_supply_created
