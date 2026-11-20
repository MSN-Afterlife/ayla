import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import discord

from bot.client import create_bot
from bot.config import Settings
from bot.services.moderation_embed_config import (
    DEFAULT_MODERATION_EMBEDS,
    MODERATION_ACTIONS,
    ModerationEmbedConfigStore,
)
from bot.services.moderation_service import ModerationService, format_duration, parse_duration
from bot.services.site_api import SiteApiServer


class FakeRequest:
    def __init__(self, *, headers=None, match_info=None, body=None, multipart_field=None):
        self.headers = headers or {}
        self.match_info = match_info or {}
        self._body = body
        self._multipart_field = multipart_field

    async def json(self):
        return self._body

    async def multipart(self):
        return FakeMultipartReader(self._multipart_field)


class FakeMultipartField:
    def __init__(self, name: str, filename: str, data: bytes):
        self.name = name
        self.filename = filename
        self._data = data

    async def read(self, decode: bool = False):
        return self._data


class FakeMultipartReader:
    def __init__(self, field):
        self._field = field
        self._yielded = False

    async def next(self):
        if self._yielded:
            return None
        self._yielded = True
        return self._field


class ModerationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "moderation.sqlite3"
        self.service = ModerationService(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_duration_parser_and_formatter(self):
        self.assertEqual(parse_duration("10m"), timedelta(minutes=10))
        self.assertEqual(parse_duration("1h30m"), timedelta(minutes=90))
        self.assertEqual(parse_duration("2d"), timedelta(days=2))
        self.assertEqual(format_duration(5400), "1h 30m")

        with self.assertRaises(ValueError):
            parse_duration("0m")
        with self.assertRaises(ValueError):
            parse_duration("29d")
        with self.assertRaises(ValueError):
            parse_duration("invalido")

    def test_cases_warns_and_log_channel(self):
        c1 = self.service.record_case(
            guild_id=10,
            user_id=200,
            moderator_id=99,
            action="warn",
            reason="Flood no chat",
        )
        c2 = self.service.record_case(
            guild_id=10,
            user_id=200,
            moderator_id=99,
            action="mute",
            reason="Spam continuo",
            duration_seconds=600,
        )
        self.assertEqual(self.service.count_active_warns(10, 200), 1)
        cases = self.service.list_user_cases(10, 200)
        self.assertEqual([c.id for c in cases], [c2.id, c1.id])

        self.assertTrue(self.service.deactivate_warn(10, c1.id))
        self.assertEqual(self.service.count_active_warns(10, 200), 0)

        self.service.record_case(guild_id=10, user_id=200, moderator_id=99, action="warn", reason="Outro")
        self.assertEqual(self.service.clear_user_warns(10, 200), 1)
        self.assertEqual(self.service.count_active_warns(10, 200), 0)

        self.assertIsNone(self.service.get_log_channel_id(10))
        self.service.set_log_channel_id(10, 555)
        self.assertEqual(self.service.get_log_channel_id(10), 555)
        self.service.clear_log_channel_id(10)
        self.assertIsNone(self.service.get_log_channel_id(10))


class ModerationEmbedConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = ModerationEmbedConfigStore(
            self.root / "moderation_embeds.json",
            self.root / "moderation_assets",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_defaults_and_template_rendering(self):
        all_configs = self.store.get_all()
        self.assertEqual(set(all_configs.keys()), set(MODERATION_ACTIONS))

        updated = self.store.update_action(
            "ban",
            {
                "title": "Martelo do Ban em {user}",
                "description": "Motivo: {reason} | Caso #{case_id}",
                "color": "#112233",
                "imageUrl": "https://example.com/ban.gif",
                "dmUser": True,
            },
        )
        self.assertEqual(updated["color"], "#112233")
        self.assertTrue(updated["dmUser"])

        embed, attachment = self.store.build_embed(
            "ban",
            {"user": "Fulano", "reason": "Regra 1", "case_id": 42},
            user_avatar_url="https://example.com/avatar.png",
        )
        self.assertIsNone(attachment)
        self.assertEqual(embed.title, "Martelo do Ban em Fulano")
        self.assertEqual(embed.description, "Motivo: Regra 1 | Caso #42")
        self.assertEqual(embed.color.value, 0x112233)
        self.assertEqual(embed.image.url, "https://example.com/ban.gif")
        self.assertEqual(embed.thumbnail.url, "https://example.com/avatar.png")

    def test_local_gif_asset_attaches_discord_file(self):
        asset_url = self.store.save_asset("mute", "silencio.gif", b"GIF89a-fake-bytes")
        self.assertEqual(asset_url, "/api/moderation/embeds/assets/mute.gif")

        embed, attachment = self.store.build_embed(
            "mute",
            {"user_mention": "<@1>", "duration": "10m", "reason": "Teste", "moderator_mention": "<@2>", "case_id": 7, "guild": "Ayla"},
        )
        self.assertIsInstance(attachment, discord.File)
        self.assertEqual(embed.image.url, "attachment://mute.gif")

        reset = self.store.reset_action("mute")
        self.assertEqual(reset["title"], DEFAULT_MODERATION_EMBEDS["mute"]["title"])
        self.assertIsNone(reset["imageUrl"])


class ModerationSiteApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.server = SiteApiServer(
            Settings(
                "test-token",
                site_api_key="daily-key",
                site_admin_api_key="admin-secret",
                moderation_embeds_path=str(self.root / "moderation_embeds.json"),
                moderation_assets_dir=str(self.root / "moderation_assets"),
                moderation_database_path=str(self.root / "moderation.sqlite3"),
            ),
            readiness_check=lambda: True,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_admin_auth_required_for_moderation_embed_mutations(self):
        res_unauth = await self.server._update_moderation_embed(
            FakeRequest(
                headers={"Authorization": "Bearer wrong"},
                match_info={"action": "ban"},
                body={"title": "Novo"},
            )
        )
        self.assertEqual(res_unauth.status, 401)

        res_ok = await self.server._update_moderation_embed(
            FakeRequest(
                headers={"Authorization": "Bearer admin-secret"},
                match_info={"action": "ban"},
                body={"title": "Banido!", "description": "{user_mention} saiu.", "color": "#FF0000"},
            )
        )
        self.assertEqual(res_ok.status, 200)
        payload = json.loads(res_ok.text)
        self.assertEqual(payload["config"]["title"], "Banido!")

        upload_res = await self.server._upload_moderation_embed_image(
            FakeRequest(
                headers={"Authorization": "Bearer admin-secret"},
                match_info={"action": "ban"},
                multipart_field=FakeMultipartField("file", "banhammer.gif", b"GIF89a123"),
            )
        )
        self.assertEqual(upload_res.status, 200)
        upload_payload = json.loads(upload_res.text)
        self.assertEqual(upload_payload["imageUrl"], "/api/moderation/embeds/assets/ban.gif")

        list_res = await self.server._moderation_embeds_list(FakeRequest())
        self.assertEqual(list_res.status, 200)
        list_payload = json.loads(list_res.text)
        self.assertEqual(list_payload["actions"]["ban"]["imageUrl"], "/api/moderation/embeds/assets/ban.gif")


class ModerationCommandRegistrationTests(unittest.TestCase):
    def test_moderation_commands_are_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bot = create_bot(
                Settings(
                    discord_token="test-token",
                    site_api_enabled=False,
                    lavalink_enabled=False,
                    levels_database_path=str(root / "levels.sqlite3"),
                    chat_config_path=str(root / "chat_config.json"),
                    presence_config_path=str(root / "presence_config.json"),
                    uno_monsters_path=str(root / "monsters_catalog.json"),
                    moderation_embeds_path=str(root / "moderation_embeds.json"),
                    moderation_assets_dir=str(root / "moderation_assets"),
                    moderation_database_path=str(root / "moderation.sqlite3"),
                    lastfm_enabled=False,
                    lastfm_database_path=str(root / "lastfm.sqlite3"),
                    openai_api_key="test-openai-key",
                )
            )

        for name in ("ban", "unban", "kick", "mute", "unmute", "warn", "historico", "removerwarn", "limparwarns", "purge", "slowmode", "lock", "unlock"):
            self.assertIsNotNone(bot.get_command(name), f"Prefix command missing: {name}")
            self.assertIsNotNone(bot.tree.get_command(name), f"Slash command missing: {name}")
        self.assertIsNotNone(bot.tree.get_command("modconfig"))


if __name__ == "__main__":
    unittest.main()
