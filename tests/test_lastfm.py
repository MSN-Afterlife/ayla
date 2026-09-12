import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import base64
import sqlite3

from bot.services.lastfm_repository import LastFmRepository
from bot.services.lastfm_service import make_api_sig
from bot.services.lastfm_scrobble import eligible_after
from bot.services.lastfm_scrobble import LastFmScrobbler

TEST_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii").rstrip("=")


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
            db_path = Path(directory) / "lastfm.sqlite3"
            repo = LastFmRepository(str(db_path), TEST_ENCRYPTION_KEY)
            repo.save_account(123, "first", "secret-key")
            repo.save_account(123, "second", "secret-key-2")
            self.assertEqual(repo.get_account(123).username, "second")
            self.assertEqual(repo.get_account(123).session_key, "secret-key-2")
            db = sqlite3.connect(db_path)
            try:
                stored_key = db.execute("SELECT lastfm_session_key FROM lastfm_accounts WHERE discord_user_id=123").fetchone()[0]
            finally:
                db.close()
            self.assertTrue(stored_key.startswith("enc:v1:"))
            self.assertNotIn("secret-key", stored_key)
            repo.disable(123)
            self.assertFalse(repo.get_account(123).scrobble_enabled)

    def test_existing_plaintext_keys_migrate_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "lastfm.sqlite3"
            LastFmRepository(str(db_path))
            db = sqlite3.connect(db_path)
            try:
                db.execute(
                    "INSERT INTO lastfm_accounts VALUES (?, ?, ?, 1, 'requested', ?, ?)",
                    (123, "legacy", "legacy-session-secret", 1, 1),
                )
                db.commit()
            finally:
                db.close()
            repo = LastFmRepository(str(db_path), TEST_ENCRYPTION_KEY)
            self.assertEqual(repo.get_account(123).session_key, "legacy-session-secret")
            db = sqlite3.connect(db_path)
            try:
                stored_key = db.execute("SELECT lastfm_session_key FROM lastfm_accounts WHERE discord_user_id=123").fetchone()[0]
            finally:
                db.close()
            self.assertTrue(stored_key.startswith("enc:v1:"))

    def test_plaintext_keys_are_never_returned_without_a_configured_key(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "lastfm.sqlite3"
            LastFmRepository(str(db_path))
            db = sqlite3.connect(db_path)
            try:
                db.execute(
                    "INSERT INTO lastfm_accounts VALUES (?, ?, ?, 1, 'requested', ?, ?)",
                    (123, "legacy", "legacy-session-secret", 1, 1),
                )
                db.commit()
            finally:
                db.close()
            repo = LastFmRepository(str(db_path))
            with self.assertRaisesRegex(RuntimeError, "must be migrated"):
                repo.get_account(123)

    def test_eligibility_rule(self):
        self.assertIsNone(eligible_after(29))
        self.assertEqual(eligible_after(31), 15.5)
        self.assertEqual(eligible_after(600), 240)

    def test_scrobble_is_attempted_for_all_voice_listeners(self):
        account = lambda user_id, name: SimpleNamespace(
            discord_user_id=user_id, username=name, session_key="key", scrobble_enabled=True
        )
        repository = SimpleNamespace(get_account=lambda user_id: account(user_id, f"user-{user_id}"))
        service = SimpleNamespace(available=True, repository=repository, scrobble=AsyncMock(), update_now_playing=AsyncMock())

        async def run():
            scrobbler = LastFmScrobbler(service)
            track = SimpleNamespace(artist="Artist", title="Title", album=None, duration=180)
            scrobbler.started(1, track, 10, 1, listener_ids=(10, 20))
            playback = scrobbler._current[1]
            playback.last_position = 90
            await scrobbler._scrobble(playback)
            self.assertEqual(service.scrobble.await_count, 2)
            self.assertTrue(scrobbler.diagnostic(1, 20)["result"].startswith("sim"))

        import asyncio
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
