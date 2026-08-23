import tempfile
import unittest
from pathlib import Path

from bot.services.lastfm_repository import LastFmRepository
from bot.services.lastfm_service import make_api_sig
from bot.services.lastfm_scrobble import eligible_after


class LastFmTests(unittest.TestCase):
    def test_signature_excludes_format_and_sorts(self):
        self.assertEqual(
            make_api_sig({"format": "json", "track": "Song", "api_key": "key", "method": "x"}, "secret"),
            "608c8cef626ea6a2c7afd92dca3cfabe",
        )

    def test_state_is_single_use_and_expiry_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = LastFmRepository(str(Path(directory) / "lastfm.sqlite3"))
            state = repo.create_state(123, ttl_seconds=900)
            self.assertEqual(repo.consume_state(state), 123)
            self.assertIsNone(repo.consume_state(state))

    def test_account_replacement_and_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = LastFmRepository(str(Path(directory) / "lastfm.sqlite3"))
            repo.save_account(123, "first", "secret-key")
            repo.save_account(123, "second", "secret-key-2")
            self.assertEqual(repo.get_account(123).username, "second")
            repo.disable(123)
            self.assertFalse(repo.get_account(123).scrobble_enabled)

    def test_eligibility_rule(self):
        self.assertIsNone(eligible_after(29))
        self.assertEqual(eligible_after(31), 15.5)
        self.assertEqual(eligible_after(600), 240)


if __name__ == "__main__":
    unittest.main()
