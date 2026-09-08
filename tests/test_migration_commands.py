import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from bot.client import create_bot
from bot.commands.migration import (
    _DatasetPolicyView,
    _PlanActionView,
    _ReplaceTargetConfirmationView,
    _SourceSelectionView,
    _confirm,
    _execute_target,
    _format_plan,
    _format_inspection,
    _is_admin,
    _migration_id_autocomplete,
    _plan,
    _plan_action_view,
    _player_autocomplete,
    _replace_target_policy,
    _reply,
    _request_confirmation,
    _resolve_plan_request,
    _resolve_target,
    _rollback_by_selector,
    _safe_multi_source_plan,
    _status,
    _status_by_selector,
)
from bot.config import Settings
from bot.services.migration_audit import MigrationAuditStore
from bot.services.migration_confirmation import MigrationConfirmationStore
from bot.services.migration_engine_client import (
    LockState,
    MigrationEngineInvalidResponse,
    MigrationExecution,
    MigrationPlan,
    MigrationPlanRequest,
    MigrationPreviewResult,
    MigrationReference,
    MigrationRollback,
    MigrationSource,
    MigrationState,
    MigrationStatus,
    PlannedOperation,
    PlayerReference,
    PresenceState,
)


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
            status="READY",
        )
        self.migrations = []
        self.players = []
        self.status_calls = []
        self.preview_request = None

    async def preview(self, request):
        self.preview_request = request
        return MigrationPreviewResult(sources=())

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
            status="READY",
        )
        await _plan(self.target, client, self.audit, MigrationPlanRequest(discord_user_id="123"))
        view = self.target.send.await_args.kwargs["view"]
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        self.assertEqual(client.status_calls, ["mig-correta"])
        self.assertIn("Confirmacao criada", interaction.response.send_message.await_args.args[0])

    async def test_nick_com_java_e_bedrock_exige_selecao_explicita(self):
        client = FakeClient(status())
        client.players = [
            PlayerReference(player_name="Mounkass", platform="java", external_id="b557a9ca-2b8c-40ba-b9fb-6595cb50247e", username="Mounkass", discord_user_id="884592686101852232"),
            PlayerReference(player_name=".Mounkass", platform="bedrock", external_id="2535408009867685", username=".Mounkass", discord_user_id="884592686101852232"),
        ]
        result = await _resolve_plan_request(self.target, client, self.audit, "Mounkass", None)
        self.assertIsNone(result)
        view = self.target.send.await_args.kwargs["view"]
        self.assertIsInstance(view, _SourceSelectionView)
        self.assertEqual({source.platform for source in view.sources}, {"java", "bedrock"})

    async def test_selecao_java_mais_bedrock_monta_payload_multi_source(self):
        client = FakeClient(status())
        view = _SourceSelectionView(
            client,
            self.audit,
            None,
            "Mounkass",
            (MigrationSource("java", "java-id", "Mounkass"), MigrationSource("bedrock", "xuid", ".Mounkass")),
            "884592686101852232",
        )
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        await confirm_view.replace_button.callback(interaction)
        self.assertEqual([source.platform for source in client.plan_request.sources], ["java", "bedrock"])
        self.assertEqual(client.plan_request.target["discord_user_id"], "884592686101852232")
        self.assertEqual(client.plan_request.dataset_policy["strategy"], "REPLACE_TARGET")
        self.assertEqual(client.plan_request.dataset_policy["source"], {"platform": "java", "external_id": "java-id"})
        self.assertTrue(client.plan_request.dataset_policy["confirmed_replace_target"])

    async def test_multiple_java_sources_renders_explicit_selection_buttons(self):
        client = FakeClient(status())
        legacy_source = MigrationSource("java", "0515e221-32d9-3337-bd41-6f5133736827", "Ben2149", source_type="legacy")
        linked_source = MigrationSource("java", "51ec26ac-cb29-42f4-9284-b7eeb60b9485", "Rubens", source_type="linked")
        view = _SourceSelectionView(
            client,
            self.audit,
            None,
            "Ben2149",
            (legacy_source, linked_source),
            "1066179464528138260",
        )
        self.assertEqual(len(view.children), 3)
        self.assertIn("Ben2149", view.children[0].label)
        self.assertIn("legacy", view.children[0].label)
        self.assertIn("Rubens", view.children[1].label)
        self.assertIn("linked", view.children[1].label)

        # Operator chooses the legacy source explicitly
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertEqual(confirm_view.source.external_id, "0515e221-32d9-3337-bd41-6f5133736827")
        await confirm_view.replace_button.callback(interaction)
        self.assertEqual(client.plan_request.dataset_policy["source"]["external_id"], "0515e221-32d9-3337-bd41-6f5133736827")
        self.assertEqual(client.plan_request.dataset_policy["strategy"], "REPLACE_TARGET")

    async def test_multi_source_plano_inseguro_nao_mostra_execute(self):
        plan = MigrationPlan(
            migration_id="mig-unsafe",
            source_identities=("java-id", "bedrock-id"),
            canonical_target="00000000-0000-0000-0009-01f0adc9f1a5",
            sources=(MigrationSource("java", "java-id"), MigrationSource("bedrock", "bedrock-id")),
            operations=(PlannedOperation("playerdata", "rewrite_uuid"),),
            status="READY",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
            target_discord_user_id="884592686101852232",
            dataset_policy={"playerdata": "java", "advancements": "java", "stats": "java"},
        )
        self.assertFalse(_safe_multi_source_plan(plan))

    async def test_multi_source_plano_ready_guarda_id_exato_no_botao(self):
        client = FakeClient(status())
        client.plan_value = MigrationPlan(
            migration_id="mig-multi-ready",
            source_identities=("java-id", "bedrock-id"),
            canonical_target="eedbb797-3373-5174-a91b-77553e3b58e4",
            sources=(MigrationSource("java", "b5579", "Mounkass"), MigrationSource("bedrock", "2535", ".Mounkass")),
            operations=(PlannedOperation("identity", "CREATE_CANONICAL_IDENTITY"), PlannedOperation("identity", "ATTACH_JAVA_IDENTITY"), PlannedOperation("identity", "ATTACH_BEDROCK_IDENTITY"), PlannedOperation("playerdata", "MOVE_FILE"), PlannedOperation("playerdata", "ARCHIVE_FILE")),
            status="READY",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
            target_discord_user_id="884592686101852232",
            dataset_policy={"playerdata": "java", "advancements": "java", "stats": "java"},
        )
        request = MigrationPlanRequest(sources=client.plan_value.sources, target={"discord_user_id": "884592686101852232"}, dataset_policy=client.plan_value.dataset_policy)
        await _plan(self.target, client, self.audit, request)
        view = self.target.send.await_args.kwargs["view"]
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        self.assertEqual(client.status_calls, ["mig-multi-ready"])

    async def test_policy_java_preserva_as_duas_sources(self):
        client = FakeClient(status())
        view = _DatasetPolicyView(
            client,
            self.audit,
            (MigrationSource("java", "java-id"), MigrationSource("bedrock", "bedrock-id")),
            {"discord_user_id": "884592686101852232"},
            "policy test",
        )
        interaction = fake_interaction(42, self.confirmations)
        await view.children[0].callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        await confirm_view.replace_button.callback(interaction)
        self.assertEqual([source.platform for source in client.plan_request.sources], ["java", "bedrock"])
        self.assertEqual(client.plan_request.dataset_policy["strategy"], "REPLACE_TARGET")
        self.assertEqual(client.plan_request.dataset_policy["source"], {"platform": "java", "external_id": "java-id"})

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

    def test_source_selection_excludes_canonical_target_and_labels_legacy(self):
        canonical = MigrationSource("canonical", "047398a1-4e44-4efc-92af-a28e1fd59e86", "MdA", source_type="canonical")
        legacy = MigrationSource("java", "033da90b-ad57-3894-a162-52cd829b137e", "Dafakn", source_type="legacy")
        linked = MigrationSource("java", "linked-java", "MdA", source_type="linked")
        view = _SourceSelectionView(FakeClient(status()), self.audit, None, "MdA", (canonical, legacy, linked), "42")
        self.assertEqual(len(view.sources), 2)
        self.assertIn("legacy", view.children[0].label.casefold())

    async def test_reply_normal_sem_view_nao_envia_view_none(self):
        interaction = fake_interaction(42, self.confirmations, deferred=False)
        await _reply(interaction, "ok")
        self.assertNotIn("view", interaction.response.send_message.await_args.kwargs)
        self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])

    async def test_reply_deferred_sem_view_nao_envia_view_none(self):
        interaction = fake_interaction(42, self.confirmations, deferred=True)
        await _reply(interaction, "ok")
        self.assertNotIn("view", interaction.followup.send.await_args.kwargs)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_reply_normal_com_view_preserva_view(self):
        interaction = fake_interaction(42, self.confirmations, deferred=False)
        view = discord.ui.View()
        await _reply(interaction, "ok", view=view)
        self.assertIs(interaction.response.send_message.await_args.kwargs["view"], view)

    async def test_reply_deferred_com_view_preserva_view(self):
        interaction = fake_interaction(42, self.confirmations, deferred=True)
        view = discord.ui.View()
        await _reply(interaction, "ok", view=view)
        self.assertIs(interaction.followup.send.await_args.kwargs["view"], view)

    async def test_source_selection_view_tem_botao_comparar_progresso(self):
        client = FakeClient(status())
        view = _SourceSelectionView(
            client,
            self.audit,
            None,
            "Mounkass",
            (MigrationSource("java", "java-id", "Mounkass"), MigrationSource("bedrock", "xuid", ".Mounkass")),
            "884592686101852232",
        )
        self.assertEqual(len(view.children), 3)
        compare_btn = view.children[2]
        self.assertEqual(compare_btn.label, "📊 Comparar progresso")
        interaction = fake_interaction(42, self.confirmations)
        await compare_btn.callback(interaction)
        self.assertIsNotNone(client.preview_request)
        self.assertEqual(client.preview_request.target["discord_user_id"], "884592686101852232")

    async def test_dataset_policy_view_tem_botao_comparar_progresso(self):
        client = FakeClient(status())
        view = _DatasetPolicyView(
            client,
            self.audit,
            (MigrationSource("java", "java-id"), MigrationSource("bedrock", "bedrock-id")),
            {"discord_user_id": "884592686101852232"},
            "policy test",
        )
        self.assertEqual(len(view.children), 3)
        compare_btn = view.children[2]
        self.assertEqual(compare_btn.label, "📊 Comparar progresso")
        interaction = fake_interaction(42, self.confirmations)
        await compare_btn.callback(interaction)
        self.assertIsNotNone(client.preview_request)


    def test_format_plan_with_structural_operations_without_dataset(self):
        plan = MigrationPlan(
            migration_id="mig-1234567890abcdef",
            source_identities=("src-java",),
            canonical_target="target-uuid",
            operations=(
                PlannedOperation(dataset=None, action="ATTACH_JAVA_IDENTITY", detail="Attach java"),
                PlannedOperation(dataset="playerdata", action="MOVE_FILE", detail="Move playerdata"),
            ),
            affected_datasets=("playerdata",),
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.UNLOCKED,
            rollback_available=True,
        )
        formatted = _format_plan(plan)
        self.assertIn("Operacoes: `ATTACH_JAVA_IDENTITY`, `playerdata:MOVE_FILE`", formatted)
        self.assertNotIn("None:", formatted)

    def test_audit_store_legacy_schema_migration_and_row_preservation(self):
        db_path = Path(self.directory.name) / "legacy_audit.sqlite3"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE minecraft_migration_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    details TEXT,
                    operator_id TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO minecraft_migration_audit_events (timestamp, event_type, user_id, details, operator_id)
                VALUES ('2026-09-07T12:00:00Z', 'LEGACY_EVENT', '123456789', 'legacy error detail', '987654321')
                """
            )
            conn.commit()

        # Initialize store on legacy DB - triggers migration
        store = MigrationAuditStore(db_path)

        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(minecraft_migration_audit_events)").fetchall()}
            expected_cols = {
                "id", "event_type", "discord_user_id", "canonical_uuid", "migration_id",
                "presence", "lock_state", "result", "error", "metadata_json", "created_at"
            }
            self.assertTrue(expected_cols.issubset(cols))

            row = conn.execute("SELECT * FROM minecraft_migration_audit_events WHERE id=1").fetchone()
            self.assertIsNotNone(row)
            # Map legacy columns: user_id -> discord_user_id, timestamp -> created_at, details -> error
            row_dict = dict(zip([c[1] for c in conn.execute("PRAGMA table_info(minecraft_migration_audit_events)").fetchall()], row))
            self.assertEqual(row_dict["event_type"], "LEGACY_EVENT")
            self.assertEqual(row_dict["discord_user_id"], "123456789")
            self.assertEqual(row_dict["created_at"], "2026-09-07T12:00:00Z")
            self.assertEqual(row_dict["error"], "legacy error detail")

        # Verify new writes succeed
        store.record("NEW_EVENT", discord_user_id="999888", result="ok")
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT count(*) FROM minecraft_migration_audit_events").fetchone()[0]
            self.assertEqual(count, 2)

    async def test_audit_store_failure_does_not_crash_plan(self):
        # Point audit to a read-only or invalid directory to simulate DB failure
        broken_audit = MigrationAuditStore(Path(self.directory.name) / "normal.sqlite3")
        broken_audit._connect = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error"))

        # Calling record directly must not raise
        broken_audit.record("TEST", discord_user_id="123", result="failed", error="something")

        # Calling _plan with a client error + broken audit must not crash the callback
        failing_client = FakeClient(status())
        failing_client.plan = AsyncMock(side_effect=MigrationEngineInvalidResponse("Invalid plan response"))

        interaction = fake_interaction(42, self.confirmations)
        request = MigrationPlanRequest(canonical_uuid="test-uuid")

        await _plan(interaction, failing_client, broken_audit, request)
        # Verify interaction received error reply despite audit failure
        self.assertTrue(interaction.response.send_message.called or interaction.followup.send.called)

    async def test_source_selection_view_defers_interaction(self):
        client = FakeClient(status())
        client.plan = AsyncMock(return_value=MigrationPlan(
            migration_id="mig-123",
            source_identities=("src",),
            canonical_target="target",
            operations=(),
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.UNLOCKED,
        ))
        view = _SourceSelectionView(
            client,
            self.audit,
            None,
            "KekseGb",
            (MigrationSource("java", "uuid-1", "KekseGb"),),
            "1509541527159050374",
        )
        interaction = fake_interaction(42, self.confirmations)

        java_btn = view.children[0]  # Usar progresso Java
        await java_btn.callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        await confirm_view.replace_button.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

    def test_format_plan_renders_execution_mode_hot(self):
        plan = MigrationPlan(
            migration_id="mig-hot-1",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            execution_mode="HOT",
            status="READY",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        rendered = _format_plan(plan)
        self.assertIn("Modo: `HOT`", rendered)
        self.assertIn("⚡ Pode ser migrado sem reiniciar o servidor.", rendered)

    def test_format_plan_renders_execution_mode_quiescent(self):
        plan = MigrationPlan(
            migration_id="mig-quiescent-1",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            execution_mode="QUIESCENT",
            status="READY",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        rendered = _format_plan(plan)
        self.assertIn("Modo: `QUIESCENT`", rendered)
        self.assertIn("🛠 Esta migração exige janela de manutenção.", rendered)

    def test_plan_action_view_suppresses_execute_button_when_status_not_ready(self):
        client = FakeClient(status())
        plan = MigrationPlan(
            migration_id="mig-blocked",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            status="BLOCKED",
            blockers=(),
            conflicts=(),
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        view = _plan_action_view(client, self.audit, plan, "42")
        self.assertIsNone(view)

    def test_plan_action_view_suppresses_execute_button_when_blockers(self):
        client = FakeClient(status())
        plan = MigrationPlan(
            migration_id="mig-blockers",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            status="READY",
            blockers=("player is online",),
            conflicts=(),
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        view = _plan_action_view(client, self.audit, plan, "42")
        self.assertIsNone(view)

    def test_plan_action_view_suppresses_execute_button_when_conflicts(self):
        client = FakeClient(status())
        plan = MigrationPlan(
            migration_id="mig-conflicts",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            status="READY",
            blockers=(),
            conflicts=("dataset conflict",),
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        view = _plan_action_view(client, self.audit, plan, "42")
        self.assertIsNone(view)

    def test_plan_action_view_returns_view_when_ready(self):
        client = FakeClient(status())
        plan = MigrationPlan(
            migration_id="mig-ready",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            status="READY",
            blockers=(),
            conflicts=(),
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        view = _plan_action_view(client, self.audit, plan, "42")
        self.assertIsNotNone(view)
        self.assertIsInstance(view, _PlanActionView)

    async def test_plan_action_view_execute_button_defense_in_depth_blocked(self):
        client = FakeClient(status())
        plan = MigrationPlan(
            migration_id="mig-defense",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            status="BLOCKED",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        view = _PlanActionView(client, self.audit, self.confirmations, plan, "42")
        interaction = fake_interaction(42, self.confirmations)
        await view.execute_button.callback(interaction)
        self.assertIn("Este plano possui impedimentos", interaction.response.send_message.await_args.args[0])

    async def test_plan_action_view_execute_button_defense_in_depth_non_admin(self):
        client = FakeClient(status())
        plan = MigrationPlan(
            migration_id="mig-defense-admin",
            source_identities=("src",),
            canonical_target="canonical-uuid",
            status="READY",
            rollback_available=True,
            presence=PresenceState.OFFLINE_CONFIRMED,
            lock_state=LockState.ABSENT,
        )
        view = _PlanActionView(client, self.audit, self.confirmations, plan, "42")
        interaction = fake_interaction(42, self.confirmations, admin=False)
        await view.execute_button.callback(interaction)
        self.assertIn("Voce nao tem permissao administrativa", interaction.response.send_message.await_args.args[0])

    def test_replace_target_policy_structure(self):
        source = MigrationSource("java", "java-uuid", "Player1")
        policy = _replace_target_policy(source)
        self.assertEqual(policy["strategy"], "REPLACE_TARGET")
        self.assertEqual(policy["source"], {"platform": "java", "external_id": "java-uuid"})
        self.assertTrue(policy["confirmed_replace_target"])
        self.assertEqual(policy["global_preserve"], ["simplelogin"])

    def test_replace_target_confirmation_view_rendering(self):
        client = FakeClient(status())
        source = MigrationSource("java", "java-uuid", "Player1")
        view = _ReplaceTargetConfirmationView(
            client, self.audit, source, "123456", "Player1", "test reason", "42"
        )
        content = view.render_content()
        self.assertIn("Fonte selecionada: `java:Player1`", content)
        self.assertIn("⚠️ O progresso atual da conta unificada será substituído.", content)
        self.assertIn("Um snapshot será criado antes da migração e poderá ser usado para rollback.", content)

    async def test_replace_target_confirmation_view_cancel_button(self):
        client = FakeClient(status())
        source = MigrationSource("java", "java-uuid", "Player1")
        view = _ReplaceTargetConfirmationView(
            client, self.audit, source, "123456", "Player1", "test reason", "42"
        )
        interaction = fake_interaction(42, self.confirmations)
        await view.cancel_button.callback(interaction)
        self.assertIn("Migração cancelada. Nenhum dado foi alterado.", interaction.response.send_message.await_args.args[0])
        for child in view.children:
            self.assertTrue(child.disabled)

    async def test_replace_target_confirmation_view_other_operator_blocked(self):
        client = FakeClient(status())
        source = MigrationSource("java", "java-uuid", "Player1")
        view = _ReplaceTargetConfirmationView(
            client, self.audit, source, "123456", "Player1", "test reason", "42"
        )
        interaction = fake_interaction(99, self.confirmations)
        await view.replace_button.callback(interaction)
        self.assertIn("Este componente pertence a outro operador.", interaction.response.send_message.await_args.args[0])

    async def test_replace_target_confirmation_view_non_admin_blocked(self):
        client = FakeClient(status())
        source = MigrationSource("java", "java-uuid", "Player1")
        view = _ReplaceTargetConfirmationView(
            client, self.audit, source, "123456", "Player1", "test reason", "42"
        )
        interaction = fake_interaction(42, self.confirmations, admin=False)
        await view.replace_button.callback(interaction)
        self.assertIn("Voce nao tem permissao administrativa", interaction.response.send_message.await_args.args[0])

    async def test_replace_target_confirmation_view_expired_blocked(self):
        client = FakeClient(status())
        source = MigrationSource("java", "java-uuid", "Player1")
        view = _ReplaceTargetConfirmationView(
            client, self.audit, source, "123456", "Player1", "test reason", "42"
        )
        view.expires_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        interaction = fake_interaction(42, self.confirmations)
        await view.replace_button.callback(interaction)
        self.assertIn("Este componente expirou.", interaction.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()


def fake_interaction(user_id, confirmations, *, admin=True, manage_guild=True, deferred=False):
    done = deferred

    async def fake_defer(*args, **kwargs):
        nonlocal done
        done = True

    permissions = SimpleNamespace(administrator=admin, manage_guild=manage_guild)
    response = SimpleNamespace(
        is_done=lambda: done,
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
        defer=AsyncMock(side_effect=fake_defer),
    )
    followup = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(edit=AsyncMock())
    client = SimpleNamespace(_migration_confirmations=confirmations)
    return SimpleNamespace(user=SimpleNamespace(id=user_id, guild_permissions=permissions), response=response, followup=followup, message=message, client=client)
