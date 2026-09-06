import tempfile
import unittest
from pathlib import Path

from bot.client import create_bot
from bot.config import Settings


class ClientCommandRegistrationTests(unittest.TestCase):
    def _settings(self, directory: str) -> Settings:
        root = Path(directory)
        return Settings(
            discord_token="test-token",
            site_api_enabled=False,
            lavalink_enabled=False,
            levels_database_path=str(root / "levels.sqlite3"),
            chat_config_path=str(root / "chat_config.json"),
            presence_config_path=str(root / "presence_config.json"),
            uno_monsters_path=str(root / "monsters_catalog.json"),
            lastfm_enabled=False,
            lastfm_database_path=str(root / "lastfm.sqlite3"),
            openai_api_key="test-openai-key",
        )

    def test_statusayla_prefix_and_slash_group_are_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            bot = create_bot(self._settings(directory))

        self.assertIsNotNone(bot.get_command("statusayla"))
        self.assertIsNotNone(bot.get_command("presenca"))
        self.assertIsNotNone(bot.tree.get_command("statusayla"))


if __name__ == "__main__":
    unittest.main()
