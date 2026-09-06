import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from bot.client import create_bot
from bot.commands.migration import _confirm, _execute_target, _is_admin, _migration_id_autocomplete, _plan, _player_autocomplete, _request_confirmation, _resolve_target, _rollback_by_selector, _status, _status_by_selector
from bot.config import Settings
from bot.services.migration_audit import MigrationAuditStore
from bot.services.migration_confirmation import MigrationConfirmationStore
from bot.services.migration_engine_client import LockState, MigrationExecution, MigrationPlan, MigrationPlanRequest, MigrationReference, MigrationRollback, MigrationState, MigrationStatus, PlayerReference, PresenceState


class FakeClient:
    def __init__(self, status):
        self.status_value = status
        self.executed = []
        self.rolled_back = []
        self.plan_value = MigrationPlan(
            migration_id="mig-1",
            source_identities=("source",),
            canonical_target="canonical",
            affected_datasets=("playerdata",),
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        self.migrations = []
        self.players = []
        self.status_calls = []

    async def status(self, migration_id):
        self.status_calls.append(migration_id)
        return self.status_value

    async def plan(self, request):
        self.plan_request = request
        return self.plan_value

    async def list_migrations(self, **kwargs):
        self.list_kwargs = kwargs
        return tuple(self.migrations)

    async def search_players(self, query, *, limit=10):
        self.player_search = (query, limit)
        return tuple(self.players)

    async def execute(self, migration_id, *, operator_id, idempotency_key=None):
        self.executed.append((migration_id, operator_id, idempotency_key))
        return MigrationExecution(
            migration_id=migration_id,
            result="executed",
            migrated_datasets=("playerdata",),
            verifications=("ok",),
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.UNLOCKED,
        )

    async def rollback(self, migration_id, *, operator_id, idempotency_key=None):
        self.rolled_back.append((migration_id, operator_id, idempotency_key))
        return MigrationRollback(
            migration_id=migration_id,
            result="rolled_back",
            restored_data=("playerdata",),
            verifications=("ok",),
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.UNLOCKED,
        )


def status(
    *,
    presence=PresenceState.OFFLINE_CONFIRMED,
    lock_state=LockState.ABSENT,
    blockers=(),
    rollback_available=True,
):
    return MigrationStatus(
        migration_id="mig-1",
        state=MigrationState.PLANNED,
        presence=presence,
        lock_state=lock_state,
        verification_state="ready",
        rollback_available=rollback_available,
        blockers=blockers,
    )


class MigrationCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.audit = MigrationAuditStore(Path(self.directory.name) / "audit.sqlite3")
        self.target = SimpleNamespace(author=SimpleNamespace(id=42), send=AsyncMock())
        self.now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
        self.confirmations = MigrationConfirmationStore(ttl_seconds=60, now=lambda: self.now[0])

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_online_blocks_execute(self):
        client = FakeClient(status(presence=PresenceState.ONLINE))
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "execute", "mig-1")
        self.assertFalse(client.executed)
        self.assertIn("ONLINE", self.target.send.await_args.args[0])

    async def test_unknown_blocks_execute(self):
        client = FakeClient(status(presence=PresenceState.UNKNOWN))
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "execute", "mig-1")
        self.assertFalse(client.executed)
        self.assertIn("UNKNOWN", self.target.send.await_args.args[0])

    async def test_offline_confirmed_reaches_confirmation(self):
        client = FakeClient(status())
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "execute", "mig-1")
        self.assertIn("Nada foi executado ainda", self.target.send.await_args.args[0])

    async def test_confirmation_expired_is_blocked(self):
        client = FakeClient(status())
        pending = self.confirmations.create(action="execute", migration_id="mig-1", operator_id="42")
        self.now[0] += timedelta(seconds=61)
        await _confirm(self.target, client, self.audit, self.confirmations, "execute", "mig-1", pending.token)
        self.assertFalse(client.executed)
        self.assertIn("expirada", self.target.send.await_args.args[0])

    async def test_confirmation_other_discord_user_is_blocked(self):
        client = FakeClient(status())
        pending = self.confirmations.create(action="execute", migration_id="mig-1", operator_id="99")
        await _confirm(self.target, client, self.audit, self.confirmations, "execute", "mig-1", pending.token)
        self.assertFalse(client.executed)
        self.assertIn("outro operador", self.target.send.await_args.args[0])

    async def test_confirmation_other_migration_id_is_blocked(self):
        client = FakeClient(status())
        pending = self.confirmations.create(action="execute", migration_id="mig-2", operator_id="42")
        await _confirm(self.target, client, self.audit, self.confirmations, "execute", "mig-1", pending.token)
        self.assertFalse(client.executed)
        self.assertIn("outra migration", self.target.send.await_args.args[0])

    async def test_consumed_confirmation_cannot_be_reused(self):
        client = FakeClient(status())
        pending = self.confirmations.create(action="execute", migration_id="mig-1", operator_id="42")
        await _confirm(self.target, client, self.audit, self.confirmations, "execute", "mig-1", pending.token)
        await _confirm(self.target, client, self.audit, self.confirmations, "execute", "mig-1", pending.token)
        self.assertEqual(len(client.executed), 1)
        self.assertIn("inexistente", self.target.send.await_args.args[0])

    async def test_blockers_impedem_execute(self):
        client = FakeClient(status(blockers=("duplicate uuid",)))
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "execute", "mig-1")
        self.assertFalse(client.executed)
        self.assertIn("blockers", self.target.send.await_args.args[0])

    async def test_gateway_lock_conflitante_impede_execute(self):
        client = FakeClient(status(lock_state=LockState.MIGRATING))
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "execute", "mig-1")
        self.assertFalse(client.executed)
        self.assertIn("Gateway Lock", self.target.send.await_args.args[0])

    async def test_rollback_exige_confirmacao(self):
        client = FakeClient(status())
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "rollback", "mig-1")
        self.assertFalse(client.rolled_back)
        self.assertIn("Nada foi executado ainda", self.target.send.await_args.args[0])

    async def test_rollback_confirmed_calls_engine(self):
        client = FakeClient(status())
        pending = self.confirmations.create(action="rollback", migration_id="mig-1", operator_id="42")
        await _confirm(self.target, client, self.audit, self.confirmations, "rollback", "mig-1", pending.token)
        self.assertEqual(len(client.rolled_back), 1)
        self.assertIn("rolled_back", self.target.send.await_args.args[0])

    async def test_usuario_sem_permissao_e_bloqueado_by_helper(self):
        permissions = SimpleNamespace(administrator=False, manage_guild=True)
        self.assertFalse(_is_admin(SimpleNamespace(guild_permissions=permissions)))

    async def test_engine_not_configured_does_not_block_startup(self):
        settings = Settings(
            "token",
            levels_database_path=str(Path(self.directory.name) / "ayla.sqlite3"),
            chat_config_path=str(Path(self.directory.name) / "chat.json"),
            site_api_enabled=False,
            openai_api_key="test",
        )
        bot = create_bot(settings)
        self.assertIsNotNone(bot.get_command("migration"))
        self.assertFalse(bot._migration_engine_client.configured)
        self.assertIsNotNone(discord.utils.get(bot.tree.get_commands(), name="migration"))

    async def test_plan_botao_execute_usa_migration_id_correto(self):
        client = FakeClient(status())
        client.plan_value = MigrationPlan(
            migration_id="mig-correta",
            source_identities=("source",),
            canonical_target="canonical",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        await _plan(self.target, client, self.audit, MigrationPlanRequest(discord_user_id="123"))
        view = self.target.send.await_args.kwargs["view"]
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        self.assertEqual(client.status_calls, ["mig-correta"])
        self.assertIn("Confirmacao criada", interaction.response.send_message.await_args.args[0])

    async def test_botao_expirado(self):
        client = FakeClient(status())
        await _plan(self.target, client, self.audit, MigrationPlanRequest(discord_user_id="123"))
        view = self.target.send.await_args.kwargs["view"]
        view.expires_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        self.assertFalse(client.status_calls)
        self.assertIn("expirou", interaction.response.send_message.await_args.args[0])

    async def test_status_por_jogador(self):
        client = FakeClient(status())
        client.migrations = [MigrationReference(migration_id="mig-player", state=MigrationState.PLANNED, player_name="Mounk")]
        await _status_by_selector(self.target, client, self.audit, selector=SimpleNamespace(payload=lambda: {"discord_user_id": "123"}))
        self.assertEqual(client.status_calls, ["mig-player"])

    async def test_rollback_por_jogador_com_exatamente_uma_candidata(self):
        client = FakeClient(status())
        client.migrations = [MigrationReference(migration_id="mig-rollback", state=MigrationState.EXECUTED, rollback_available=True)]
        await _rollback_by_selector(self.target, client, self.audit, self.confirmations, selector=SimpleNamespace(payload=lambda: {"discord_user_id": "123"}))
        self.assertEqual(client.status_calls, ["mig-rollback"])
        self.assertIn("Confirmacao criada", self.target.send.await_args.args[0])

    async def test_rollback_ambiguo_nao_escolhe_automaticamente(self):
        client = FakeClient(status())
        client.migrations = [
            MigrationReference(migration_id="mig-a", state=MigrationState.EXECUTED, rollback_available=True),
            MigrationReference(migration_id="mig-b", state=MigrationState.EXECUTED, rollback_available=True),
        ]
        await _rollback_by_selector(self.target, client, self.audit, self.confirmations, selector=SimpleNamespace(payload=lambda: {"discord_user_id": "123"}))
        self.assertFalse(client.status_calls)
        self.assertIn("mais de uma migration", self.target.send.await_args.args[0])

    async def test_autocomplete_retorna_migrations_corretas(self):
        client = FakeClient(status())
        client.migrations = [
            MigrationReference(migration_id="mig_bf4cb82cf767d195", state=MigrationState.EXECUTED, player_name="Mounk", updated_at="2026-09-06T12:00:00Z"),
        ]
        choices = await _migration_id_autocomplete(client, "moun")
        self.assertEqual(choices[0].value, "mig_bf4cb82cf767d195")
        self.assertIn("Mounk", choices[0].name)

    async def test_migration_id_explicito_continua_funcionando(self):
        client = FakeClient(status())
        await _status(self.target, client, self.audit, "mig-explicita")
        self.assertEqual(client.status_calls, ["mig-explicita"])

    async def test_execute_por_nick_usa_migration_planejada_unica(self):
        client = FakeClient(status())
        client.players = [PlayerReference(canonical_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff", player_name="Mounk")]
        client.migrations = [MigrationReference(migration_id="mig-plan", state=MigrationState.PLANNED, player_name="Mounk")]
        await _execute_target(self.target, client, self.audit, self.confirmations, "moun")
        self.assertEqual(client.status_calls, ["mig-plan"])
        self.assertIn("Confirmacao criada", self.target.send.await_args.args[0])

    async def test_execute_por_nick_ambiguo_nao_escolhe(self):
        client = FakeClient(status())
        client.players = [PlayerReference(canonical_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff", player_name="Mounk")]
        client.migrations = [
            MigrationReference(migration_id="mig-a", state=MigrationState.PLANNED, player_name="Mounk"),
            MigrationReference(migration_id="mig-b", state=MigrationState.PLANNED, player_name="Mounk"),
        ]
        await _execute_target(self.target, client, self.audit, self.confirmations, "moun")
        self.assertFalse(client.status_calls)
        self.assertIn("mais de uma migration", self.target.send.await_args.args[0])

    async def test_botao_confirmar_executa_sem_staff_copiar_id(self):
        client = FakeClient(status())
        await _request_confirmation(self.target, client, self.audit, self.confirmations, "execute", "mig-1")
        view = self.target.send.await_args.kwargs["view"]
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        self.assertEqual(client.executed[0][0], "mig-1")

    async def test_busca_por_nick_aproximado_resolve_canonical_uuid(self):
        client = FakeClient(status())
        client.players = [PlayerReference(canonical_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff", player_name="Mounk", confidence=0.91)]
        request = await _resolve_target(self.target, client, "moun")
        self.assertEqual(request.canonical_uuid, "bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        self.assertEqual(client.player_search, ("moun", 5))

    async def test_busca_por_nick_ambigua_nao_escolhe(self):
        client = FakeClient(status())
        client.players = [
            PlayerReference(canonical_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", player_name="Mounk"),
            PlayerReference(canonical_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff", player_name="MounkAlt"),
        ]
        request = await _resolve_target(self.target, client, "moun")
        self.assertIsNone(request)
        self.assertIn("mais de um jogador", self.target.send.await_args.args[0])

    async def test_autocomplete_de_nick_retorna_jogadores(self):
        client = FakeClient(status())
        client.players = [PlayerReference(canonical_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff", player_name="Mounk", confidence=0.95)]
        choices = await _player_autocomplete(client, "mou")
        self.assertEqual(choices[0].value, "player:bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        self.assertIn("Mounk", choices[0].name)


if __name__ == "__main__":
    unittest.main()


def fake_interaction(user_id, confirmations, *, admin=True, manage_guild=True):
    permissions = SimpleNamespace(administrator=admin, manage_guild=manage_guild)
    response = SimpleNamespace(is_done=lambda: False, send_message=AsyncMock())
    followup = SimpleNamespace(send=AsyncMock())
    client = SimpleNamespace(_migration_confirmations=confirmations)
    return SimpleNamespace(user=SimpleNamespace(id=user_id, guild_permissions=permissions), response=response, followup=followup, client=client)
