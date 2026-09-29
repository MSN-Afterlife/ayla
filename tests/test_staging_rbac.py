import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.commands.staging import _MinecraftAuthBypassView, _guard, _require_discord_administrator
from bot.services.staging_rbac import StagingRbacStore, can_use_admin_command, is_staging_operator


class StagingRbacTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = StagingRbacStore(Path(self.tempdir.name) / "ayla.sqlite3")
        self.guild = SimpleNamespace(id=100, get_role=lambda role_id: SimpleNamespace(id=role_id, mention=f"<@&{role_id}>") if role_id == 200 else None)

    def tearDown(self):
        self.tempdir.cleanup()

    def member(self, *, user_id=1, admin=False, role_ids=(), guild=None):
        return SimpleNamespace(
            id=user_id,
            guild=guild or self.guild,
            roles=[SimpleNamespace(id=role_id) for role_id in role_ids],
            guild_permissions=SimpleNamespace(administrator=admin),
        )

    def interaction(self, member):
        response = SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
            defer=AsyncMock(),
        )
        return SimpleNamespace(user=member, guild=member.guild, response=response, followup=SimpleNamespace(send=AsyncMock()))

    def test_administrator_adds_and_operator_is_allowed(self):
        self.assertTrue(self.store.add_role(100, 200, 10))
        self.assertTrue(is_staging_operator(self.member(role_ids=(200,)), 100, self.store))

    async def test_operator_cannot_use_rbac_configuration_guard(self):
        interaction = self.interaction(self.member(role_ids=(200,)))
        self.assertFalse(await _require_discord_administrator(interaction))

    async def test_user_without_role_is_denied(self):
        self.store.add_role(100, 200, 10)
        interaction = self.interaction(self.member(role_ids=(201,)))
        self.assertFalse(await _guard(interaction, self.store))

    async def test_real_admin_is_always_allowed_without_role(self):
        self.assertTrue(is_staging_operator(self.member(admin=True), 100, self.store))

    def test_remove_revokes_immediately_and_is_guild_scoped(self):
        self.store.add_role(100, 200, 10)
        member = self.member(role_ids=(200,))
        self.assertFalse(is_staging_operator(member, 999, self.store))
        self.assertTrue(self.store.remove_role(100, 200, 10))
        self.assertFalse(is_staging_operator(member, 100, self.store))

    def test_deleted_discord_role_is_safe_and_does_not_authorize(self):
        self.store.add_role(100, 999, 10)
        self.assertFalse(is_staging_operator(self.member(role_ids=()), 100, self.store))
        self.assertEqual({999}, self.store.role_ids(100))

    def test_persists_after_store_recreation(self):
        self.store.add_role(100, 200, 10)
        reopened = StagingRbacStore(self.store.database_path)
        self.assertEqual({200}, reopened.role_ids(100))

    def test_shared_policy_allows_operator_in_staging(self):
        self.store.add_role(100, 200, 10)
        member = self.member(role_ids=(200,))
        bot = SimpleNamespace(_settings=SimpleNamespace(environment="staging"), _staging_rbac=self.store)
        context = SimpleNamespace(author=member, guild=self.guild, bot=bot)
        self.assertTrue(can_use_admin_command(context, required="administrator"))
        self.assertTrue(can_use_admin_command(context, required="manage_guild"))

    def test_shared_policy_does_not_leak_staging_role_to_production(self):
        self.store.add_role(100, 200, 10)
        member = self.member(role_ids=(200,), admin=False)
        bot = SimpleNamespace(_settings=SimpleNamespace(environment="production"), _staging_rbac=self.store)
        context = SimpleNamespace(author=member, guild=self.guild, bot=bot)
        self.assertFalse(can_use_admin_command(context, required="administrator"))
        self.assertFalse(can_use_admin_command(context, required="manage_guild"))

    def test_shared_policy_preserves_production_native_permission(self):
        member = self.member(admin=True)
        bot = SimpleNamespace(_settings=SimpleNamespace(environment="production"), _staging_rbac=self.store)
        context = SimpleNamespace(author=member, guild=self.guild, bot=bot)
        self.assertTrue(can_use_admin_command(context, required="administrator"))

    async def test_button_revalidates_role_and_owner(self):
        self.store.add_role(100, 200, 10)
        view = _MinecraftAuthBypassView(AsyncMock(), "42", enabled=True, rbac_store=self.store, guild_id=100)
        owner = self.interaction(self.member(user_id=42, role_ids=(200,)))
        other = self.interaction(self.member(user_id=43, role_ids=(200,)))
        self.assertTrue(await view._allowed(owner))
        self.assertFalse(await view._allowed(other))
        self.store.remove_role(100, 200, 10)
        self.assertFalse(await view._allowed(owner))


if __name__ == "__main__":
    unittest.main()
