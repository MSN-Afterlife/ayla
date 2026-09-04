import tempfile
import threading
import unittest
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.services.minecraft_identity import (
    MinecraftConflict,
    MinecraftIdentityStore,
    MinecraftLinkError,
    normalize_minecraft_canonical_name,
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


if __name__ == "__main__":
    unittest.main()
