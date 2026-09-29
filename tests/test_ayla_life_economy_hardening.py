import concurrent.futures
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ayla_life.economy import AccountStatus, AccountType, EconomicKernel, CurrencyStatus


class HardeningCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "kernel.sqlite3"
        self.kernel = EconomicKernel(self.path)
        self.currency = self.kernel.create_currency("Hardening Test", "HTK", decimal_places=2)
        self.cb = self.kernel.create_account(AccountType.CENTRAL_BANK, account_id="cb")
        self.alice = self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="alice", account_id="alice")
        self.bob = self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="bob", account_id="bob")
        self.carol = self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="carol", account_id="carol")

    def tearDown(self):
        self.kernel.close()
        self.temp.cleanup()

    def issue(self, account="alice", amount=10_000, key="issue"):
        return self.kernel.issue(account, self.currency.id, amount, key, authority_account="cb")

    def test_all_account_types_and_duplicate_owner_pair_policy(self):
        for account_type in AccountType:
            account = self.kernel.create_account(account_type, owner_type="TEST", owner_id=account_type.value, account_id=f"type-{account_type.value}")
            self.assertEqual(account.account_type, account_type)
        with self.assertRaises(ValueError):
            self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", account_id="bad-owner")
        with self.assertRaises(ValueError):
            self.kernel.create_account(AccountType.PLAYER, owner_id="missing-type", account_id="bad-owner-2")
        with self.assertRaises(ValueError):
            self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="alice", account_id="alice")

    def test_currency_validation_and_retired_behavior(self):
        with self.assertRaises(ValueError):
            self.kernel.create_currency("Invalid", "BAD", decimal_places=10)
        retired = self.kernel.create_currency("Retired", "OLD", status=CurrencyStatus.RETIRED)
        with self.assertRaises(ValueError):
            self.kernel.transfer("alice", "bob", retired.id, 1, "retired-transfer")
        self.issue()
        self.kernel.set_account_status("alice", AccountStatus.FROZEN)
        with self.assertRaises(ValueError):
            self.kernel.transfer("alice", "bob", self.currency.id, 1, "frozen-send")

    def test_strict_amount_key_and_metadata_validation(self):
        for amount in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                self.kernel.transfer("alice", "bob", self.currency.id, amount, f"amount-{amount}")
        with self.assertRaises(ValueError):
            self.kernel.transfer("alice", "bob", self.currency.id, 1, "")
        with self.assertRaises(ValueError):
            self.kernel.transfer("alice", "bob", self.currency.id, 1, "x" * 256)
        self.kernel.issue("alice", self.currency.id, 10_000, "meta-issue", authority_account="cb", metadata={"source": "test", "nested": {"ok": True}})
        with self.assertRaises(ValueError):
            self.kernel.transfer("alice", "bob", self.currency.id, 1, "bad-meta", metadata={"bad": float("nan")})

    def test_system_accounts_cannot_be_used_by_normal_transfer(self):
        self.issue()
        with closing(sqlite3.connect(self.path)) as db:
            system_id = db.execute("SELECT id FROM accounts WHERE id LIKE 'system:issuance:%'").fetchone()[0]
        with self.assertRaises(ValueError):
            self.kernel.transfer(system_id, "bob", self.currency.id, 1, "system-transfer")

    def test_reversal_of_issuance_burn_and_genesis_updates_supply(self):
        issued = self.issue(amount=1000, key="issue-reverse")
        self.kernel.reverse(issued.id, "reverse-issued")
        genesis = self.kernel.genesis("bob", self.currency.id, 500, "genesis-reverse", authority_account="cb")
        burned = self.kernel.burn("bob", self.currency.id, 200, "burn-reverse", authority_account="cb")
        self.kernel.reverse(burned.id, "reverse-burned")
        self.kernel.reverse(genesis.id, "reverse-genesis")
        supply = self.kernel.money_supply(self.currency.id)
        self.assertEqual((supply.issued, supply.burned, supply.genesis, supply.total_supply), (0, 0, 0, 0))
        self.assertTrue(self.kernel.reconcile().ok)

    def test_same_key_replay_works_after_authority_is_frozen(self):
        first = self.issue(amount=100, key="authority-replay")
        self.kernel.set_account_status("cb", AccountStatus.FROZEN)
        replay = self.issue(amount=100, key="authority-replay")
        self.assertEqual(first.id, replay.id)

    def test_concurrent_reversal_has_one_winner(self):
        self.issue()
        original = self.kernel.transfer("alice", "bob", self.currency.id, 500, "reversal-target")
        def attempt(index):
            try:
                return self.kernel.reverse(original.id, f"reverse-race-{index}").id
            except (ValueError, sqlite3.IntegrityError, sqlite3.OperationalError):
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            result = [value for value in pool.map(attempt, range(8)) if value]
        self.assertEqual(len(set(result)), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger_transactions WHERE reversal_of=?", (original.id,)).fetchone()[0], 1)

    def test_failure_injection_rolls_back_every_stage(self):
        for stage in ("after_transaction_insert", "after_ledger_entry", "after_balance_update", "before_audit", "before_commit"):
            self.kernel.close()
            def fail(current, expected=stage):
                if current == expected:
                    raise RuntimeError(expected)
            self.kernel = EconomicKernel(self.path, failure_hook=fail)
            with self.assertRaises(RuntimeError):
                self.kernel.issue("alice", self.currency.id, 100, f"failure-{stage}", authority_account="cb")
            self.kernel.close()
            self.kernel = EconomicKernel(self.path)
            self.assertEqual(self.kernel.get_balance("alice", self.currency.id), 0)
            self.assertTrue(self.kernel.reconcile().ok)
            with closing(sqlite3.connect(self.path)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger_transactions WHERE idempotency_key=?", (f"failure-{stage}",)).fetchone()[0], 0)

    def test_failed_transaction_key_can_be_retried_after_restart(self):
        self.kernel.close()
        def fail(stage):
            if stage == "before_commit":
                raise RuntimeError(stage)
        self.kernel = EconomicKernel(self.path, failure_hook=fail)
        with self.assertRaises(RuntimeError):
            self.kernel.issue("alice", self.currency.id, 100, "retry-after-failure", authority_account="cb")
        self.kernel.close()
        self.kernel = EconomicKernel(self.path)
        retry = self.kernel.issue("alice", self.currency.id, 100, "retry-after-failure", authority_account="cb")
        self.assertEqual(retry.status.value, "POSTED")
        self.assertEqual(self.kernel.get_balance("alice", self.currency.id), 100)

    def test_restart_preserves_balances_supply_audit_and_idempotency(self):
        tx = self.issue(amount=1000, key="restart-issue")
        self.kernel.transfer("alice", "bob", self.currency.id, 250, "restart-transfer")
        before = self.kernel.money_supply(self.currency.id)
        self.kernel.close()
        self.kernel = EconomicKernel(self.path)
        replay = self.kernel.issue("alice", self.currency.id, 1000, "restart-issue", authority_account="cb")
        self.assertEqual(tx.id, replay.id)
        self.assertEqual(self.kernel.get_balance("alice", self.currency.id), 750)
        self.assertEqual(self.kernel.get_balance("bob", self.currency.id), 250)
        self.assertEqual(before, self.kernel.money_supply(self.currency.id))
        self.assertTrue(self.kernel.reconcile().ok)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertGreater(db.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 0)

    def test_direct_database_attacks_are_blocked_or_detectable(self):
        tx = self.issue()
        self.kernel.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE ledger_transactions SET description='tampered' WHERE id=?", (tx.id,))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM ledger_entries WHERE transaction_id=?", (tx.id,))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO currencies(id,name,code,decimal_places,status,created_at,metadata) VALUES ('other','Other','HTK',0,'ACTIVE','now','{}')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO ledger_entries(transaction_id,account_id,currency_id,amount,created_at) VALUES ('missing','alice',?,?,?)", (self.currency.id, 1, "now"))
        self.kernel = EconomicKernel(self.path)
        self.assertTrue(self.kernel.reconcile().ok)

    def test_reference_model_matches_deterministic_random_transfers(self):
        currencies = [self.currency, self.kernel.create_currency("Second", "H2K"), self.kernel.create_currency("Third", "H3K")]
        accounts = [self.alice.id, self.bob.id, self.carol.id]
        model = {(account, currency.id): 10_000 for account in accounts for currency in currencies}
        for index, account in enumerate(accounts):
            for currency in currencies:
                self.kernel.issue(account, currency.id, 10_000, f"model-issue-{index}-{currency.id}", authority_account="cb")
        rng = random.Random(20260915)
        for index in range(1_500):
            currency = rng.choice(currencies).id
            source, target = rng.sample(accounts, 2)
            amount = rng.randint(1, 500)
            if model[(source, currency)] < amount:
                continue
            self.kernel.transfer(source, target, currency, amount, f"model-transfer-{index}")
            model[(source, currency)] -= amount
            model[(target, currency)] += amount
        for key, expected in model.items():
            self.assertEqual(self.kernel.get_balance(*key), expected)
        self.assertTrue(self.kernel.reconcile().ok)

    def test_randomized_mixed_state_machine_is_reproducible(self):
        accounts = [self.alice.id, self.bob.id, self.carol.id]
        model = {account: 0 for account in accounts}
        rng = random.Random(42)
        self.kernel.genesis("alice", self.currency.id, 5_000, "state-genesis", authority_account="cb")
        model["alice"] += 5_000
        posted = []
        for index in range(1_000):
            source, target = rng.sample(accounts, 2)
            amount = rng.randint(1, 100)
            if model[source] >= amount:
                tx = self.kernel.transfer(source, target, self.currency.id, amount, f"state-transfer-{index}")
                model[source] -= amount; model[target] += amount; posted.append((tx, source, target, amount))
            if index and index % 100 == 0 and posted:
                tx, source, target, amount = posted[-1]
                self.kernel.reverse(tx.id, f"state-reverse-{index}")
                model[source] += amount; model[target] -= amount
        for account, expected in model.items():
            self.assertEqual(self.kernel.get_balance(account, self.currency.id), expected)
        self.assertTrue(self.kernel.reconcile().ok)


if __name__ == "__main__":
    unittest.main()
