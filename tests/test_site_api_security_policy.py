import unittest

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.config import Settings
from bot.services.site_api import (
    API_SCOPE_NAMES,
    _api_security_middleware,
    _cors_middleware,
    _parse_allowed_origins,
    api_scope_for,
)


class SiteApiSecurityPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_registered_route_families_have_explicit_scopes(self):
        routes = {
            "/health": "public",
            "/api/site": "public",
            "/api/uno/monsters": "public",
            "/api/uno/monsters/schema": "public",
            "/api/uno/monsters/assets/example.png": "public",
            "/api/admin/uno/monsters": "site-admin",
            "/api/admin/uno/monsters/example": "site-admin",
            "/api/admin/uno/monsters/example/image": "site-admin",
            "/api/admin/uno/monsters/assets/example.png": "site-admin",
            "/api/daily-ayla": "site-user",
            "/auth/lastfm/callback": "public",
            "/internal/minecraft/link/request": "minecraft",
            "/internal/minecraft/account/java/123": "minecraft",
        }
        self.assertEqual(set(API_SCOPE_NAMES), {"public", "site-user", "site-admin", "minecraft", "migration"})
        for path, expected in routes.items():
            with self.subTest(path=path):
                self.assertEqual(api_scope_for(path), expected)
        self.assertIsNone(api_scope_for("/unregistered"))

    async def test_auth_scope_and_cors_allowlist_are_centralized(self):
        settings = Settings(
            discord_token="test",
            site_api_key="site-user-secret",
            site_admin_api_key="site-admin-secret",
            ayla_minecraft_internal_token="minecraft-secret",
        )
        app = web.Application(middlewares=[_cors_middleware("https://msnafterlife.online, *"), _api_security_middleware(settings)])

        async def ok(_request):
            return web.json_response({"ok": True})

        app.router.add_get("/api/uno/monsters", ok)
        app.router.add_post("/api/admin/uno/monsters", ok)
        app.router.add_post("/api/daily-ayla", ok)
        app.router.add_get("/internal/minecraft/account/java/{external_id}", ok)
        async with TestClient(TestServer(app)) as client:
            denied = await client.post("/api/admin/uno/monsters")
            self.assertEqual(denied.status, 401)
            allowed = await client.post(
                "/api/admin/uno/monsters",
                headers={"Authorization": "Bearer site-admin-secret", "Origin": "https://msnafterlife.online"},
            )
            self.assertEqual(allowed.status, 200)
            self.assertEqual(allowed.headers.get("Access-Control-Allow-Origin"), "https://msnafterlife.online")
            denied_origin = await client.get("/api/uno/monsters", headers={"Origin": "https://attacker.example"})
            self.assertNotIn("Access-Control-Allow-Origin", denied_origin.headers)
            oversized = await client.post(
                "/api/admin/uno/monsters",
                headers={"Authorization": "Bearer site-admin-secret"},
                data=b"x" * (1024 * 1024 + 1),
            )
            self.assertEqual(oversized.status, 413)
            self.assertNotIn("*", _parse_allowed_origins("*"))


if __name__ == "__main__":
    unittest.main()
