import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bot.config import Settings
from bot.services.site_api import SiteApiServer


class FakeRequest:
    def __init__(self, headers=None, payload=None):
        self.headers = headers or {}
        self.payload = payload or {"discordUserId": "123456789"}
        self.json_read = False

    async def json(self):
        self.json_read = True
        return self.payload


class SiteApiDailyAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = SiteApiServer(
            Settings(
                "test-token",
                site_api_key="daily-site-key",
                site_admin_api_key="separate-admin-key",
                uno_monsters_path=str(Path(self.temp_dir.name) / "monsters.json"),
            ),
            readiness_check=lambda: True,
        )
        self.server._economy.claim_daily = Mock(
            return_value=SimpleNamespace(
                profile=SimpleNamespace(balance=1500, daily_streak=3),
                remaining_seconds=0,
                amount=100,
                bonus=20,
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_daily_requires_configured_bearer_and_rejects_other_credentials(self):
        for headers in (
            {},
            {"Authorization": "Bearer wrong-key"},
            {"x-api-key": "daily-site-key"},
            {"Authorization": "Bearer separate-admin-key"},
        ):
            request = FakeRequest(headers=headers)
            response = await self.server._daily(request)
            self.assertEqual(response.status, 401)
            self.assertFalse(request.json_read)
        self.server._economy.claim_daily.assert_not_called()

    async def test_daily_is_disabled_when_site_api_key_is_missing(self):
        self.server._settings = Settings(
            "test-token",
            site_api_key=None,
            site_admin_api_key="separate-admin-key",
            uno_monsters_path=str(Path(self.temp_dir.name) / "monsters.json"),
        )
        request = FakeRequest(headers={"Authorization": "Bearer arbitrary-client-key"})

        response = await self.server._daily(request)

        self.assertEqual(response.status, 503)
        self.assertFalse(request.json_read)
        self.server._economy.claim_daily.assert_not_called()

    async def test_daily_accepts_only_the_configured_site_bearer(self):
        request = FakeRequest(headers={"Authorization": "bEaReR daily-site-key"})

        response = await self.server._daily(request)

        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.text)["ok"])
        self.server._economy.claim_daily.assert_called_once_with(123456789, bypass_cooldown=False)


if __name__ == "__main__":
    unittest.main()
