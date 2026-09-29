import json
import tempfile
import unittest
from pathlib import Path

from bot.config import Settings
from bot.services.site_api import SiteApiServer


class FakeRequest:
    def __init__(self, headers=None, match_info=None):
        self.headers = headers or {}
        self.match_info = match_info or {"monster_id": "test-monster"}

    async def json(self):
        raise AssertionError("Unauthorized mutation read its request body")

    async def multipart(self):
        raise AssertionError("Unauthorized upload read its request body")


class SiteApiAdminAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temp_dir.name) / "monsters.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_server(self, *, admin_key=None, daily_key="daily-only-key"):
        return SiteApiServer(
            Settings(
                "test-token",
                site_api_key=daily_key,
                site_admin_api_key=admin_key,
                uno_monsters_path=str(self.catalog_path),
            ),
            readiness_check=lambda: True,
        )

    def mutation_handlers(self, server):
        return (
            server._create_monster,
            server._update_monster,
            server._delete_monster,
            server._upload_monster_image,
        )

    async def assert_mutations_unauthorized(self, server, headers):
        for handler in self.mutation_handlers(server):
            response = await handler(FakeRequest(headers=headers))
            self.assertEqual(response.status, 401)

    async def test_mutations_fail_closed_when_admin_key_is_not_configured(self):
        server = self.make_server(admin_key=None)

        await self.assert_mutations_unauthorized(
            server, {"Authorization": "Bearer daily-only-key"}
        )
        self.assertFalse(self.catalog_path.exists())

    async def test_mutations_reject_missing_invalid_and_daily_keys(self):
        server = self.make_server(admin_key="separate-admin-key")

        for headers in (
            {},
            {"Authorization": "Bearer wrong-key"},
            {"Authorization": "Bearer daily-only-key"},
            {"x-api-key": "separate-admin-key"},
        ):
            await self.assert_mutations_unauthorized(server, headers)
        self.assertFalse(self.catalog_path.exists())

    async def test_valid_admin_bearer_can_mutate_catalog(self):
        server = self.make_server(admin_key="separate-admin-key")
        server._save_monsters([{"id": "test-monster", "name": "Test"}])

        response = await server._delete_monster(
            FakeRequest(
                headers={"Authorization": "bEaReR separate-admin-key"},
                match_info={"monster_id": "test-monster"},
            )
        )

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(self.catalog_path.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
