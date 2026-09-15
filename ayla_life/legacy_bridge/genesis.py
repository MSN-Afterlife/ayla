from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ayla_life.economy import AccountType, CurrencyStatus, EconomicKernel
from ayla_life.economy.models import Account
from ayla_life.economy.repository import utc_now

from .models import LegacyProfile, LegacySnapshot, LegacyValidationReport, ValidationSeverity


@dataclass(frozen=True)
class WinkCurrencySpec:
    name: str = "Wink"
    code: str = "WINK"
    decimal_places: int = 0
    emoji: str | None = None
    status: CurrencyStatus = CurrencyStatus.ACTIVE
    source: str = "legacy_economy"


@dataclass(frozen=True)
class GenesisManifest:
    id: str
    snapshot_id: str
    currency_code: str
    source: str
    source_row_count: int
    source_total: int
    planned_accounts: int
    planned_transactions: int
    status: str
    created_at: str
    validated_at: str | None
    applied_at: str | None
    content_hash: str
    content_hash_algorithm: str = "AYLA_LEGACY_CONTENT_HASH_V1"
    schema_fingerprint_algorithm: str = "AYLA_LEGACY_SCHEMA_FINGERPRINT_V1"


@dataclass(frozen=True)
class GenesisAccountPlan:
    account_id: str
    legacy_user_id: int
    owner_type: str
    owner_id: str


@dataclass(frozen=True)
class GenesisTransactionPlan:
    idempotency_key: str
    legacy_user_id: int
    account_id: str
    amount: int
    currency_code: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class GenesisPlan:
    snapshot_id: str
    snapshot_content_hash: str
    currency: WinkCurrencySpec
    accounts: tuple[GenesisAccountPlan, ...]
    transactions: tuple[GenesisTransactionPlan, ...]
    valid_count: int
    warning_count: int
    blocked_count: int
    legacy_total: int
    manifest: GenesisManifest

    @property
    def ready_for_cutover(self) -> bool:
        return self.blocked_count == 0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["currency"]["status"] = self.currency.status.value
        result["manifest"]["status"] = self.manifest.status
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False)


@dataclass(frozen=True)
class GenesisDryRunReport:
    snapshot_id: str
    currency_code: str
    profiles: int
    valid: int
    warnings: int
    blocked: int
    legacy_total: int
    genesis_total: int
    player_total: int
    mismatches: tuple[dict[str, Any], ...]
    reconciliation_ok: bool
    idempotency_replay_ok: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False)


class LegacyGenesisPlanner:
    """Turns a validated immutable snapshot into a deterministic, unapplied plan."""

    def __init__(self, currency: WinkCurrencySpec | None = None) -> None:
        self.currency = currency or WinkCurrencySpec()

    @staticmethod
    def account_id(user_id: int) -> str:
        return f"player:discord:{user_id}"

    def plan(self, snapshot: LegacySnapshot, validation: LegacyValidationReport) -> GenesisPlan:
        if snapshot.snapshot_id != validation.snapshot_id:
            raise ValueError("snapshot and validation do not match")
        accounts = tuple(GenesisAccountPlan(self.account_id(profile.user_id), profile.user_id, "DISCORD_USER", str(profile.user_id)) for profile in validation.valid_profiles)
        transactions = tuple(
            GenesisTransactionPlan(
                f"legacy-genesis:{snapshot.snapshot_id}:{profile.user_id}:{self.currency.code}",
                profile.user_id,
                self.account_id(profile.user_id),
                int(profile.balance),
                self.currency.code,
                {
                    "origin": "LEGACY_ECONOMY",
                    "provenance": "PRE_AYLA_LIFE_UNKNOWN",
                    "snapshot_id": snapshot.snapshot_id,
                    "legacy_user_id": profile.user_id,
                    "legacy_balance": int(profile.balance),
                    "daily_streak": profile.daily_streak,
                    "last_daily_at": profile.last_daily_at,
                    "legacy_updated_at": profile.updated_at,
                },
            )
            for profile in validation.valid_profiles if profile.balance > 0
        )
        status = "VALIDATED" if validation.blocked_count == 0 else "PLANNED"
        manifest = GenesisManifest(
            id=f"manifest:{snapshot.snapshot_id}:{self.currency.code}",
            snapshot_id=snapshot.snapshot_id,
            currency_code=self.currency.code,
            source=snapshot.source,
            source_row_count=len(snapshot.profiles),
            source_total=validation.legacy_total,
            planned_accounts=len(accounts),
            planned_transactions=len(transactions),
            status=status,
            created_at=utc_now(),
            validated_at=utc_now() if validation.blocked_count == 0 else None,
            applied_at=None,
            content_hash=snapshot.content_hash,
            content_hash_algorithm=snapshot.content_hash_algorithm,
            schema_fingerprint_algorithm=snapshot.schema_fingerprint_algorithm,
        )
        return GenesisPlan(snapshot.snapshot_id, snapshot.content_hash, self.currency, accounts, transactions, validation.valid_count, validation.warning_count, validation.blocked_count, validation.legacy_total, manifest)


class GenesisDryRunner:
    """Applies a plan only to a temporary Ayla Life database, then verifies it."""

    def run(self, plan: GenesisPlan) -> GenesisDryRunReport:
        if plan.blocked_count:
            raise ValueError("blocked validation issues prevent Genesis dry-run")
        with TemporaryDirectory(prefix="ayla-life-genesis-") as directory:
            database = Path(directory) / "dry_run.sqlite3"
            with EconomicKernel(database) as kernel:
                currency = kernel.create_currency(plan.currency.name, plan.currency.code, emoji=plan.currency.emoji, decimal_places=plan.currency.decimal_places, status=plan.currency.status, metadata={"source": plan.currency.source}, currency_id="currency:WINK")
                authority = kernel.create_account(AccountType.CENTRAL_BANK, owner_type="SYSTEM", owner_id="GENESIS_AUTHORITY", account_id="system:genesis-authority")
                accounts: dict[int, Account] = {}
                for account_plan in plan.accounts:
                    accounts[account_plan.legacy_user_id] = kernel.create_account(AccountType.PLAYER, owner_type=account_plan.owner_type, owner_id=account_plan.owner_id, account_id=account_plan.account_id)
                transaction_ids: dict[str, str] = {}
                for tx_plan in plan.transactions:
                    tx = kernel.genesis(tx_plan.account_id, currency.id, tx_plan.amount, tx_plan.idempotency_key, authority_account=authority.id, metadata=tx_plan.metadata, description=f"Legacy Genesis for user {tx_plan.legacy_user_id}")
                    transaction_ids[tx_plan.idempotency_key] = tx.id
                for tx_plan in plan.transactions:
                    replay = kernel.genesis(tx_plan.account_id, currency.id, tx_plan.amount, tx_plan.idempotency_key, authority_account=authority.id, metadata=tx_plan.metadata, description=f"Legacy Genesis for user {tx_plan.legacy_user_id}")
                    if replay.id != transaction_ids[tx_plan.idempotency_key]:
                        raise AssertionError("Genesis idempotency replay returned a different transaction")
                mismatches: list[dict[str, Any]] = []
                for account_plan in plan.accounts:
                    expected = next((tx.amount for tx in plan.transactions if tx.legacy_user_id == account_plan.legacy_user_id), 0)
                    actual = kernel.get_balance(account_plan.account_id, currency.id)
                    if actual != expected:
                        mismatches.append({"user_id": account_plan.legacy_user_id, "expected": expected, "actual": actual, "difference": actual - expected})
                reconciliation = kernel.reconcile()
                supply = kernel.money_supply(currency.id)
                player_total = sum(kernel.get_balance(account.id, currency.id) for account in accounts.values())
                genesis_total = supply.genesis
                status = "READY_FOR_CUTOVER" if not mismatches and reconciliation.ok and plan.legacy_total == genesis_total == player_total else "FAILED"
                return GenesisDryRunReport(plan.snapshot_id, currency.code, plan.manifest.source_row_count, plan.valid_count, plan.warning_count, plan.blocked_count, plan.legacy_total, genesis_total, player_total, tuple(mismatches), reconciliation.ok, True, status)


class LegacyGenesisExecutor:
    """Intentionally unavailable until a separately approved cutover."""

    def apply(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("real Genesis execution is disabled in M1 preparation")
