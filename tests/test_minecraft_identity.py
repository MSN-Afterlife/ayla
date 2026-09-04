import tempfile
import threading
import unittest
import json
import discord
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.client import AylaBot, create_bot
from bot.commands.minecraft import _status
from bot.services.minecraft_identity import (
    MinecraftConflict,
    MinecraftIdentityStore,
    MinecraftLinkError,
    normalize_minecraft_canonical_name,
    normalize_bedrock_xuid,
    valid_internal_token,
)
from bot.services.site_api import SiteApiServer


class MinecraftIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        self.settings = Settings("token", levels_database_path=str(Path(self.directory.name) / "ayla.sqlite3"), ayla_minecraft_internal_token="internal-secret")
        self.store = MinecraftIdentityStore(self.settings, now=lambda: self.clock[0])
        self.uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def tearDown(self):
        self.directory.cleanup()

    def code(self, external_id=None, username="PlayerTeste"):
        return self.store.request_link_code("java", external_id or self.uuid, username)["code"]

    def bedrock_code(self, xuid="2533274791234567", username="BedrockTag"):
        return self.store.request_link_code("BEDROCK", xuid, username)["code"]

    def test_creation_uuid_is_unique_and_stable(self):
        result = self.store.link_code("123456789", "Ayla_Dev", self.code())
        lookup = self.store.lookup_java(self.uuid)
        self.assertEqual(result["identity"]["canonical_uuid"], lookup["identity"]["canonical_uuid"])
        self.assertNotEqual(result["identity"]["canonical_uuid"], self.uuid)

    def test_name_is_minecraft_safe_and_collision_is_deterministic(self):
        self.assertEqual(normalize_minecraft_canonical_name("João 🎮/teste"), "Jooteste")
        first = self.store.link_code("1", "MesmoNome", self.code())
        other_uuid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        second = self.store.link_code("2", "MesmoNome", self.code(other_uuid))
        self.assertNotEqual(first["identity"]["canonical_name"].lower(), second["identity"]["canonical_name"].lower())
        self.assertLessEqual(len(second["identity"]["canonical_name"]), 16)

    def test_expiration_single_use_and_invalid_code(self):
        code = self.code()
        self.clock[0] += timedelta(seconds=601)
        with self.assertRaisesRegex(MinecraftLinkError, "expirado"):
            self.store.link_code("1", "User", code)
        with self.assertRaisesRegex(MinecraftLinkError, "inexistente"):
            self.store.link_code("1", "User", "ZZZZZZ")
        self.clock[0] = datetime(2026, 1, 1, tzinfo=timezone.utc)
        code = self.code("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        self.store.link_code("1", "User", code)
        with self.assertRaisesRegex(MinecraftLinkError, "consumido"):
            self.store.link_code("1", "User", code)

    def test_account_and_discord_conflicts_are_blocked(self):
        pending = self.code()
        owner_conflict = self.code()
        other_uuid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        discord_conflict = self.code(other_uuid)
        self.store.link_code("discord-a", "A", pending)
        with self.assertRaises(MinecraftConflict):
            self.store.link_code("discord-b", "B", owner_conflict)
        with self.assertRaises(MinecraftConflict):
            self.store.link_code("discord-a", "A", discord_conflict)

    def test_lookup_unlinked_and_disabled(self):
        self.assertEqual(self.store.lookup_java(self.uuid), {"linked": False})
        self.store.link_code("1", "User", self.code())
        with closing(self.store._connect()) as connection:
            connection.execute("UPDATE minecraft_identities SET enabled=0 WHERE discord_user_id='1'")
        self.assertFalse(self.store.lookup_java(self.uuid)["enabled"])

    def test_internal_token_requires_exact_value(self):
        self.assertTrue(valid_internal_token("secret", "secret"))
        self.assertFalse(valid_internal_token("secret", "Secret"))
        self.assertFalse(valid_internal_token(None, "secret"))

    def test_dual_consumption_only_one_succeeds(self):
        code = self.code()
        results = []
        def consume(discord_id):
            try:
                self.store.link_code(discord_id, discord_id, code)
                results.append("ok")
            except MinecraftLinkError:
                results.append("error")
        threads = [threading.Thread(target=consume, args=(str(i),)) for i in (1, 2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(results), ["error", "ok"])

    def test_bedrock_xuid_validation_and_canonical_storage(self):
        self.assertEqual(normalize_bedrock_xuid("0002533274791234567"), "2533274791234567")
        self.assertEqual(normalize_bedrock_xuid("18446744073709551615"), "18446744073709551615")
        for value in ("-1", "abc", "12.5", "", self.uuid, "18446744073709551616"):
            with self.assertRaises(MinecraftLinkError):
                normalize_bedrock_xuid(value)
        code = self.bedrock_code("0002533274791234567")
        result = self.store.link_code("1", "Ayla", code)
        self.assertEqual(result["minecraft_account"]["external_id"], "2533274791234567")

    def test_java_and_bedrock_share_one_identity(self):
        java = self.store.link_code("1", "Ayla", self.code())
        bedrock_uuid = "2533274791234567"
        bedrock = self.store.link_code("1", "Ayla", self.bedrock_code(bedrock_uuid))
        self.assertEqual(java["identity"]["canonical_uuid"], bedrock["identity"]["canonical_uuid"])
        self.assertTrue(self.store.lookup_account("bedrock", bedrock_uuid)["linked"])

    def test_bedrock_and_java_cross_link_and_per_platform_limits(self):
        bedrock_code = self.bedrock_code()
        java_code = self.code()
        self.store.link_code("1", "Ayla", bedrock_code)
        self.store.link_code("1", "Ayla", java_code)
        second_java = self.code("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        with self.assertRaises(MinecraftConflict):
            self.store.link_code("1", "Ayla", second_java)
        second_bedrock = self.bedrock_code("2533274791234568")
        with self.assertRaises(MinecraftConflict):
            self.store.link_code("1", "Ayla", second_bedrock)

    def test_same_bedrock_xuid_cannot_be_linked_to_another_discord(self):
        first = self.bedrock_code()
        second = self.bedrock_code()
        self.store.link_code("1", "Ayla", first)
        with self.assertRaises(MinecraftConflict):
            self.store.link_code("2", "Other", second)

    def test_bedrock_disabled_lookup_and_linked_request_include_enabled(self):
        code = self.bedrock_code()
        self.store.link_code("1", "Ayla", code)
        with closing(self.store._connect()) as connection:
            connection.execute("UPDATE minecraft_identities SET enabled=0 WHERE discord_user_id='1'")
        lookup = self.store.lookup_account("bedrock", "2533274791234567")
        linked_request = self.store.request_link_code("bedrock", "2533274791234567", "BedrockTag")
        self.assertFalse(lookup["enabled"])
        self.assertFalse(linked_request["enabled"])

    def test_dual_bedrock_consumption_only_one_succeeds(self):
        code = self.bedrock_code()
        results = []
        def consume(discord_id):
            try:
                self.store.link_code(discord_id, discord_id, code)
                results.append("ok")
            except MinecraftLinkError:
                results.append("error")
        threads = [threading.Thread(target=consume, args=(str(i),)) for i in (1, 2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(results), ["error", "ok"])


class MinecraftApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings("token", levels_database_path=str(Path(self.directory.name) / "api.sqlite3"), ayla_minecraft_internal_token="internal-secret")
        self.server = SiteApiServer(self.settings, lambda: True)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_internal_request_requires_token_and_accepts_valid_token(self):
        unauthorized = SimpleNamespace(headers={}, json=AsyncMock(return_value={}), remote="127.0.0.1")
        response = await self.server._minecraft_link_request(unauthorized)
        self.assertEqual(response.status, 401)

        request = SimpleNamespace(
            headers={"authorization": "Bearer internal-secret"},
            json=AsyncMock(return_value={"platform": "java", "external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "username": "PlayerTeste"}),
            remote="127.0.0.1",
        )
        response = await self.server._minecraft_link_request(request)
        self.assertEqual(response.status, 200)
        payload = json.loads(response.text)
        self.assertFalse(payload["linked"])
        self.assertEqual(payload["expires_in"], 600)

    async def test_account_lookup_endpoint_returns_unlinked(self):
        request = SimpleNamespace(headers={"authorization": "Bearer internal-secret"}, match_info={"external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})
        response = await self.server._minecraft_java_lookup(request)
        self.assertEqual(response.status, 200)
        self.assertIn('"linked": false', response.text)

    async def test_bedrock_and_legacy_java_lookup_routes(self):
        code = self.server._minecraft.request_link_code("bedrock", "2533274791234567", "BedrockTag")["code"]
        self.server._minecraft.link_code("1", "Ayla", code)
        bedrock_request = SimpleNamespace(headers={"authorization": "Bearer internal-secret"}, match_info={"platform": "bedrock", "external_id": "2533274791234567"})
        response = await self.server._minecraft_account_lookup(bedrock_request)
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.text)["linked"])

        java_request = SimpleNamespace(headers={"authorization": "Bearer internal-secret"}, match_info={"external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"})
        response = await self.server._minecraft_java_lookup(java_request)
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(response.text)["linked"])

    async def test_status_shows_java_and_bedrock(self):
        store = self.server._minecraft
        store.link_code("1", "Ayla", store.request_link_code("java", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "JavaTag")["code"])
        store.link_code("1", "Ayla", store.request_link_code("bedrock", "2533274791234567", "BedrockTag")["code"])
        target = SimpleNamespace(author=SimpleNamespace(id=1, display_name="Ayla"), send=AsyncMock())
        await _status(target, store)
        message = target.send.await_args.args[0]
        self.assertIn("JavaTag", message)
        self.assertIn("BedrockTag", message)


class MinecraftStartupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings = Settings(
            "test-token",
            levels_database_path=str(root / "ayla.sqlite3"),
            chat_config_path=str(root / "chat_config.json"),
            uno_monsters_path=str(root / "monsters.json"),
            site_api_enabled=True,
            ayla_minecraft_internal_token="internal-secret",
            openai_api_key="test-only-key",
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_ayla_bot_initializes_identity_store_before_site_api(self):
        bot = AylaBot(self.settings, command_prefix="a!", intents=discord.Intents.none(), help_command=None)
        self.assertIsInstance(bot._minecraft_identity_store, MinecraftIdentityStore)
        self.assertIs(bot._site_api._minecraft, bot._minecraft_identity_store)

    def test_create_bot_smoke_registers_minecraft_with_same_store(self):
        bot = create_bot(self.settings)
        self.assertIs(bot._site_api._minecraft, bot._minecraft_identity_store)
        self.assertIsNotNone(bot.get_command("minecraft"))

    def test_startup_applies_bedrock_migration(self):
        store = MinecraftIdentityStore(self.settings)
        with closing(store._connect()) as connection:
            versions = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
            index = connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?", ("minecraft_accounts_one_bedrock_per_identity",)).fetchone()
        self.assertIn("001_minecraft_identity", versions)
        self.assertIn("002_minecraft_bedrock", versions)
        self.assertIsNotNone(index)


if __name__ == "__main__":
    unittest.main()
