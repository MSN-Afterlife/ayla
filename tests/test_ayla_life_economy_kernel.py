import concurrent.futures
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ayla_life.economy import AccountStatus, AccountType, EconomicKernel


class KernelTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.kernel = EconomicKernel(Path(self.temp.name) / "economy.sqlite3")
        self.currency = self.kernel.create_currency("Test Wink", "TWK")
        self.central_bank = self.kernel.create_account(AccountType.CENTRAL_BANK, account_id="central-bank")
        self.alice = self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="alice", account_id="alice")
        self.bob = self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="bob", account_id="bob")
        self.carol = self.kernel.create_account(AccountType.PLAYER, owner_type="TEST", owner_id="carol", account_id="carol")

    def tearDown(self):
        self.kernel.close()
        self.temp.cleanup()

    def issue(self, amount=1000, key="issue-1"):
        return self.kernel.issue(self.alice.id, self.currency.id, amount, key, authority_account=self.central_bank.id)

    def test_currency_code_is_unique_and_multiple_currencies_work(self):
        with self.assertRaises(ValueError):
            self.kernel.create_currency("Duplicate", "TWK")
        other = self.kernel.create_currency("Other", "OTH")
        self.issue()
        self.assertEqual(self.kernel.get_balance(self.alice.id, self.currency.id), 1000)
        self.assertEqual(self.kernel.get_balance(self.alice.id, other.id), 0)

    def test_transfer_is_balanced_atomic_and_audited(self):
        tx = self.issue()
        transfer = self.kernel.transfer(self.alice.id, self.bob.id, self.currency.id, 400, "pay-1")
        self.assertEqual(transfer.status.value, "POSTED")
        self.assertEqual(self.kernel.get_balance(self.alice.id, self.currency.id), 600)
        self.assertEqual(self.kernel.get_balance(self.bob.id, self.currency.id), 400)
        with closing(sqlite3.connect(self.kernel.database_path)) as db:
            self.assertEqual(db.execute("SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE transaction_id=?", (transfer.id,)).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_events WHERE transaction_id=?", (transfer.id,)).fetchone()[0], 1)
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM ledger_entries WHERE transaction_id=?", (tx.id,))

    def test_validation_and_rollback(self):
        self.issue(100)
        with self.assertRaises(ValueError):
            self.kernel.transfer(self.alice.id, self.bob.id, self.currency.id, 101, "too-much")
        self.assertEqual(self.kernel.get_balance(self.alice.id, self.currency.id), 100)
        with self.assertRaises(ValueError):
            self.kernel.transfer(self.alice.id, self.bob.id, self.currency.id, 0, "zero")
        with self.assertRaises(ValueError):
            self.kernel.transfer(self.alice.id, self.alice.id, self.currency.id, 1, "self")

    def test_idempotency_survives_restart_and_rejects_payload_change(self):
        first = self.issue(500, "same")
        second = self.kernel.issue(self.alice.id, self.currency.id, 500, "same", authority_account=self.central_bank.id)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.kernel.get_balance(self.alice.id, self.currency.id), 500)
        with self.assertRaises(ValueError):
            self.kernel.issue(self.alice.id, self.currency.id, 501, "same", authority_account=self.central_bank.id)
        restarted = EconomicKernel(self.kernel.database_path)
        third = restarted.issue(self.alice.id, self.currency.id, 500, "same", authority_account=self.central_bank.id)
        self.assertEqual(first.id, third.id)

    def test_issuance_burn_genesis_and_supply_are_distinct(self):
        self.issue(1000)
        self.kernel.burn(self.alice.id, self.currency.id, 250, "burn-1", authority_account=self.central_bank.id)
        self.kernel.genesis(self.bob.id, self.currency.id, 700, "genesis-1", authority_account=self.central_bank.id)
        supply = self.kernel.money_supply(self.currency.id)
        self.assertEqual((supply.issued, supply.burned, supply.genesis), (1000, 250, 700))
        self.assertEqual(supply.net_supply_created, 750)
        self.assertEqual(supply.total_supply, 1450)

    def test_authority_and_frozen_account_rules(self):
        with self.assertRaises(PermissionError):
            self.kernel.issue(self.alice.id, self.currency.id, 1, "bad-authority", authority_account=self.alice.id)
        self.issue()
        self.kernel.set_account_status(self.alice.id, AccountStatus.FROZEN)
        with self.assertRaises(ValueError):
            self.kernel.transfer(self.alice.id, self.bob.id, self.currency.id, 1, "frozen-send")

    def test_reversal_is_compensating_and_only_once(self):
        self.issue()
        original = self.kernel.transfer(self.alice.id, self.bob.id, self.currency.id, 400, "pay-reverse")
        reversal = self.kernel.reverse(original.id, "reverse-1")
        self.assertEqual(self.kernel.get_balance(self.alice.id, self.currency.id), 1000)
        self.assertEqual(self.kernel.get_balance(self.bob.id, self.currency.id), 0)
        self.assertEqual(reversal.reversal_of, original.id)
        with self.assertRaises(ValueError):
            self.kernel.reverse(original.id, "reverse-2")
        with self.assertRaises(ValueError):
            self.kernel.reverse(reversal.id, "reverse-reversal")
        with closing(sqlite3.connect(self.kernel.database_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ledger_transactions WHERE status='POSTED'").fetchone()[0], 3)

    def test_reconciliation_detects_read_model_divergence(self):
        self.issue()
        self.assertTrue(self.kernel.reconcile().ok)
        with closing(sqlite3.connect(self.kernel.database_path)) as db:
            db.execute("UPDATE account_balances SET balance=999 WHERE account_id='alice' AND currency_id=?", (self.currency.id,))
            db.commit()
        report = self.kernel.reconcile()
        self.assertFalse(report.ok)
        self.assertEqual(report.differences[0].difference, 1)

    def test_concurrent_double_spend_only_one_transfer_posts(self):
        self.issue(800)
        def attempt(index):
            try:
                self.kernel.transfer("alice", "bob" if index == 0 else "carol", self.currency.id, 800, f"concurrent-{index}")
                return True
            except (ValueError, sqlite3.OperationalError):
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.kernel.get_balance("alice", self.currency.id), 0)
        self.assertEqual(self.kernel.get_balance("bob", self.currency.id) + self.kernel.get_balance("carol", self.currency.id), 800)

    def test_concurrent_idempotency_posts_once(self):
        self.issue()
        def attempt(_):
            try:
                return self.kernel.transfer("alice", "bob", self.currency.id, 100, "same-transfer").id
            except sqlite3.OperationalError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = [value for value in pool.map(attempt, range(8)) if value]
        self.assertTrue(ids)
        self.assertEqual(set(ids), {ids[0]})
        self.assertEqual(self.kernel.get_balance("alice", self.currency.id), 900)
        self.assertEqual(self.kernel.get_balance("bob", self.currency.id), 100)


if __name__ == "__main__":
    unittest.main()
