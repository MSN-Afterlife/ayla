import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from bot.commands.staging import _MinecraftAuthBypassView, _guard, _send_status, _set_and_confirm
from bot.services.migration_engine_client import MigrationEngineUnavailable


class FakeStagingClient:
    def __init__(self, statuses=None, *, error=None):
        self.statuses = list(statuses or [False])
        self.error = error
        self.set_calls = []

    async def staging_auth_bypass_status(self):
        if self.error:
            raise self.error
        return self.statuses.pop(0)

    async def set_staging_auth_bypass(self, enabled):
        if self.error:
            raise self.error
        self.set_calls.append(enabled)
        return enabled


class FakeRbacStore:
    def role_ids(self, guild_id):
        return {200}


class MutableResponse:
    def __init__(self):
        self.done = False
        self.send_message = AsyncMock(side_effect=self._send)
        self.defer = AsyncMock(side_effect=self._defer)

    def is_done(self):
        return self.done

    async def _send(self, *args, **kwargs):
        self.done = True

    async def _defer(self, *args, **kwargs):
        self.done = True


class StagingCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rbac = FakeRbacStore()

    async def test_status_off(self):
        interaction = fake_interaction()
        await _send_status(interaction, FakeStagingClient([False]))
        self.assertIn("ATIVA", interaction.response.send_message.await_args.args[0])
        self.assertNotIn("BYPASS ATIVO", interaction.response.send_message.await_args.args[0])

    async def test_status_on(self):
        interaction = fake_interaction()
        await _send_status(interaction, FakeStagingClient([True]))
        self.assertIn("BYPASS ATIVO", interaction.response.send_message.await_args.args[0])

    async def test_ativacao_confirmada(self):
        client = FakeStagingClient([True])
        view = _MinecraftAuthBypassView(client, "42", enabled=True, rbac_store=self.rbac, guild_id=100)
        interaction = fake_interaction(user_id=42)
        await view.children[0].callback(interaction)
        self.assertEqual(client.set_calls, [True])
        self.assertIn("ATIVADO", interaction.followup.send.await_args.args[0])

    async def test_cancelamento(self):
        client = FakeStagingClient([False])
        view = _MinecraftAuthBypassView(client, "42", enabled=True, rbac_store=self.rbac, guild_id=100)
        interaction = fake_interaction(user_id=42)
        await view.children[1].callback(interaction)
        self.assertEqual(client.set_calls, [])
        self.assertIn("cancelada", interaction.response.send_message.await_args.args[0])

    async def test_desativacao(self):
        interaction = fake_interaction()
        client = FakeStagingClient([False])
        await _set_and_confirm(interaction, client, enabled=False)
        self.assertEqual(client.set_calls, [False])
        self.assertIn("DESATIVADO", interaction.response.send_message.await_args.args[0])

    async def test_api_timeout(self):
        interaction = fake_interaction()
        client = FakeStagingClient(error=MigrationEngineUnavailable("timeout"))
        await _set_and_confirm(interaction, client, enabled=True)
        self.assertEqual(client.set_calls, [])
        self.assertIn("Nao consegui alterar", interaction.response.send_message.await_args.args[0])

    async def test_usuario_sem_permissao(self):
        interaction = fake_interaction(admin=False)
        allowed = await _guard(interaction)
        self.assertFalse(allowed)
        self.assertIn("administrador", interaction.response.send_message.await_args.args[0])

    async def test_botao_por_outro_operador_recusado(self):
        client = FakeStagingClient([True])
        view = _MinecraftAuthBypassView(client, "42", enabled=True, rbac_store=self.rbac, guild_id=100)
        interaction = fake_interaction(user_id=99)
        await view.children[0].callback(interaction)
        self.assertEqual(client.set_calls, [])
        self.assertIn("outro operador", interaction.response.send_message.await_args.args[0])

    async def test_botao_expirado(self):
        client = FakeStagingClient([True])
        view = _MinecraftAuthBypassView(client, "42", enabled=True, rbac_store=self.rbac, guild_id=100)
        view.expires_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        interaction = fake_interaction(user_id=42)
        await view.children[0].callback(interaction)
        self.assertEqual(client.set_calls, [])
        self.assertIn("expirou", interaction.response.send_message.await_args.args[0])


def fake_interaction(*, user_id=42, admin=True):
    response = MutableResponse()
    followup = SimpleNamespace(send=AsyncMock())
    permissions = SimpleNamespace(administrator=admin)
    guild = SimpleNamespace(id=100)
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, guild=guild, roles=[SimpleNamespace(id=200)], guild_permissions=permissions),
        guild=guild,
        response=response,
        followup=followup,
    )


if __name__ == "__main__":
    unittest.main()
