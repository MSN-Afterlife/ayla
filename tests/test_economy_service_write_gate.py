from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bot.config import Settings
from bot.services.bingo_service import BingoError, BingoService
from bot.services.economy_service import EconomyService
from bot.services.economy_write_gate import EconomyWriteGate, EconomyWriteRejected


class ServiceGateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "levels.sqlite3"
        self.gate_path = root / "gate.json"
        self.gate = EconomyWriteGate.bootstrap_open(self.gate_path)
        self.settings = Settings(discord_token="test", levels_database_path=str(self.db), economy_write_gate_state_path=str(self.gate_path))
        self.service = EconomyService(self.settings, write_gate=self.gate)
        self.service.add_balance(1, 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_common_service_boundary_blocks_daily_transfer_and_adjustment(self):
        self.gate.drain_and_lock()
        for operation in (
            lambda: self.service.claim_daily(1),
            lambda: self.service.transfer(1, 2, 1),
            lambda: self.service.add_balance(1, 1),
        ):
            with self.assertRaises(EconomyWriteRejected): operation()
        self.assertEqual(self.service.get_profile(1).balance, 1000)

    def test_open_allows_operations_after_reopen(self):
        self.gate.drain_and_lock(); self.gate.open(reason="test")
        self.service.transfer(1, 2, 100)
        self.assertEqual(self.service.get_profile(1).balance, 900)
        self.assertEqual(self.service.get_profile(2).balance, 100)

    def test_bingo_economic_mutations_use_gate(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT OR IGNORE INTO economy_profiles(user_id,balance,daily_streak,updated_at) VALUES(1,1000,0,1)")
            db.commit()
        bingo = BingoService(self.db, write_gate=self.gate)
        game = bingo.create(1, 1, 1, 100)
        self.gate.drain_and_lock()
        with self.assertRaises(EconomyWriteRejected): bingo.join(game, 1)
        self.assertEqual(bingo.game(game_id=game)["state"], "WAITING")


if __name__ == "__main__":
    unittest.main()
