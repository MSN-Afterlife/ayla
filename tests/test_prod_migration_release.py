import os
import unittest
from unittest import mock
from pathlib import Path

import discord

from bot.client import create_bot
from bot.config import load_settings, Settings
from bot.services.migration_engine_client import MigrationEngineClient


class ProdMigrationReleaseTests(unittest.TestCase):
    def test_compose_passes_environment_into_bot_container(self):
        compose = (Path(__file__).parents[1] / "deploy" / "compose.yml").read_text(encoding="utf-8")
        self.assertIn("AYLA_ENV: ${AYLA_ENV:?AYLA_ENV is required}", compose)

    def test_production_deploy_requires_nonempty_site_api_key(self):
        script = (Path(__file__).parents[1] / "scripts" / "deploy-remote.sh").read_text(encoding="utf-8")
        self.assertIn('if test "$environment" = prod; then', script)
        self.assertIn('require_secure_production_env "$runtime_env"', script)
        self.assertIn('require_nonempty_env_assignment SITE_API_KEY "$runtime_env"', script)

    def test_prod_config_parses_migration_engine_without_secret_defaults(self):
        env = {
            "DISCORD_TOKEN": "discord-token",
            "SITE_API_KEY": "daily-site-key",
            "MIGRATION_ENGINE_BASE_URL": "unix:/opt/ayla/prod/run/migration-engine.sock",
            "MIGRATION_ENGINE_TOKEN": "prod-token",
            "MIGRATION_ENGINE_TIMEOUT_SECONDS": "7",
            "MIGRATION_CONFIRMATION_TTL_SECONDS": "180",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        self.assertEqual("unix:/opt/ayla/prod/run/migration-engine.sock", settings.migration_engine_base_url)
        self.assertEqual("prod-token", settings.migration_engine_token)
        self.assertEqual(7, settings.migration_engine_timeout_seconds)
        self.assertEqual(180, settings.migration_confirmation_ttl_seconds)

    def test_production_rejects_missing_or_empty_site_api_key(self):
        for value in (None, "", "   "):
            env = {"DISCORD_TOKEN": "discord-token", "AYLA_ENV": "prod"}
            if value is not None:
                env["SITE_API_KEY"] = value
            with self.subTest(value=value), mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, "SITE_API_KEY"):
                    load_settings()

    def test_nonproduction_or_explicitly_disabled_site_api_allows_missing_key(self):
        cases = (
            {"DISCORD_TOKEN": "discord-token", "AYLA_ENV": "staging"},
            {
                "DISCORD_TOKEN": "discord-token",
                "AYLA_ENV": "production",
                "SITE_API_ENABLED": "false",
            },
        )
        for env in cases:
            with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                self.assertIsNone(load_settings().site_api_key)

    def test_migration_commands_are_registered_without_staging_bypass_commands(self):
        settings = Settings(
            "discord-token",
            site_api_enabled=False,
            openai_api_key="test",
            migration_engine_base_url="http://127.0.0.1:8099",
            migration_engine_token="prod-token",
        )
        bot = create_bot(settings)
        self.assertIsNotNone(bot.get_command("migration"))
        slash_names = {command.name for command in bot.tree.get_commands()}
        self.assertIn("migration", slash_names)
        self.assertIn("minecraft", slash_names)
        self.assertNotIn("minecraft_auth", slash_names)

    def test_client_configuration_does_not_log_token_in_repr(self):
        client = MigrationEngineClient("http://engine", "super-secret-token")
        self.assertNotIn("super-secret-token", repr(client))


if __name__ == "__main__":
    unittest.main()
