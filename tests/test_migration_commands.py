import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from bot.client import create_bot
from bot.commands.migration import _confirm, _is_admin, _request_confirmation
from bot.config import Settings
from bot.services.migration_audit import MigrationAuditStore
from bot.services.migration_confirmation import MigrationConfirmationStore
from bot.services.migration_engine_client import LockState, MigrationExecution, MigrationRollback, MigrationState, MigrationStatus, PresenceState


class FakeClient:
    def __init__(self, status):
        self.status_value = status
        self.executed = []
        self.rolled_back = []

    async def status(self, migration_id):
        return self.status_value

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


if __name__ == "__main__":
    unittest.main()
