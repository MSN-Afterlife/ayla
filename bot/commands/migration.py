import logging
import asyncio
import uuid
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.migration_audit import MigrationAuditStore
from bot.services.migration_confirmation import MigrationConfirmationError, MigrationConfirmationStore
from bot.services.migration_engine_client import (
    AdvancementsPreview,
    IdentitySummary,
    LockState,
    MigrationEngineAuthError,
    MigrationEngineClient,
    MigrationEngineError,
    MigrationEngineInvalidResponse,
    MigrationEngineNotConfigured,
    MigrationEngineRejected,
    MigrationEngineUnavailable,
    MigrationErrorCode,
    MigrationInspectRequest,
    MigrationPlan,
    MigrationPlanRequest,
    MigrationPreviewRequest,
    MigrationPreviewResult,
    MigrationReference,
    MigrationSource,
    MigrationState,
    MigrationStatus,
    PlayerdataPreview,
    PlayerReference,
    PresenceState,
    SourceProfilePreview,
    StatsPreview,
)
from bot.services.migration_preview_service import (
    ComparisonSummary,
    PreviewRecommendation,
    build_comparison,
    filter_relevant_items,
    format_dimension,
    format_equipment_item,
    format_item_name,
    format_playtime,
    format_position,
    format_xp,
)


logger = logging.getLogger(__name__)
UTC = timezone.utc


def setup_migration_commands(bot: commands.Bot, settings: Settings) -> MigrationEngineClient:
    client = MigrationEngineClient(
        settings.migration_engine_base_url,
        settings.migration_engine_token,
        timeout_seconds=settings.migration_engine_timeout_seconds,
    )
    confirmations = MigrationConfirmationStore(ttl_seconds=settings.migration_confirmation_ttl_seconds)
    audit = MigrationAuditStore(settings.levels_database_path)
    bot._migration_engine_client = client
    bot._migration_confirmations = confirmations
    bot._migration_audit = audit

    @bot.group(name="migration", aliases=["migracao"], invoke_without_command=True)
    async def migration_prefix(ctx: commands.Context) -> None:
        await ctx.send("Use `a!migration inspect`, `plan`, `status`, `execute`, `rollback` ou `confirm`.")

    @migration_prefix.command(name="inspect")
    @commands.has_permissions(manage_guild=True)
    async def inspect_prefix(ctx: commands.Context, target: str) -> None:
        request = await _resolve_target(ctx, client, target)
        if request:
            await _inspect(ctx, client, audit, request)

    @migration_prefix.command(name="plan")
    @commands.has_permissions(manage_guild=True)
    async def plan_prefix(ctx: commands.Context, target: str, *, reason: str = "") -> None:
        request = await _resolve_plan_request(ctx, client, audit, target, reason or None)
        if request:
            await _plan(ctx, client, audit, request)

    @migration_prefix.command(name="status")
    @commands.has_permissions(manage_guild=True)
    async def status_prefix(ctx: commands.Context, target: str) -> None:
        await _status_target(ctx, client, audit, target)

    @migration_prefix.command(name="execute")
    @commands.has_permissions(administrator=True)
    async def execute_prefix(ctx: commands.Context, migration_id: str) -> None:
        await _request_confirmation(ctx, client, audit, confirmations, "execute", migration_id)

    @migration_prefix.command(name="rollback")
    @commands.has_permissions(administrator=True)
    async def rollback_prefix(ctx: commands.Context, migration_id: str) -> None:
        await _rollback_target(ctx, client, audit, confirmations, migration_id)

    @migration_prefix.command(name="confirm")
    @commands.has_permissions(administrator=True)
    async def confirm_prefix(ctx: commands.Context, action: str, migration_id: str, token: str) -> None:
        await _confirm(ctx, client, audit, confirmations, action, migration_id, token)

    group = app_commands.Group(name="migration", description="Orquestracao de migrations Minecraft")

    async def autocomplete_migration_id(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await _migration_id_autocomplete(client, current)

    async def autocomplete_rollback_id(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await _migration_id_autocomplete(client, current, state=MigrationState.EXECUTED, rollback_available=True)

    async def autocomplete_player(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await _player_autocomplete(client, current)

    @group.command(name="inspect", description="Inspeciona uma identidade Minecraft sem alterar dados")
    @app_commands.describe(nick="Nick Minecraft do jogador")
    @app_commands.autocomplete(nick=autocomplete_player)
    async def inspect_slash(interaction: discord.Interaction, nick: str) -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para inspecionar migrations.")
            return
        await _defer(interaction)
        request = await _resolve_target(interaction, client, nick)
        if request:
            await _inspect(interaction, client, audit, request)

    @group.command(name="migrar", description="Migra sua identidade Minecraft com verificacoes de seguranca")
    @app_commands.describe(alvo="Alvo opcional; somente admins podem informar terceiros")
    async def migrar_slash(interaction: discord.Interaction, alvo: str | None = None) -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para executar migrations.")
            return
        if alvo and not _is_admin(interaction.user):
            await _reply(interaction, "Staff comum so pode migrar a propria identidade.")
            return
        await _defer(interaction)
        await _one_click_migrate(interaction, client, audit, alvo)

    @group.command(name="plan", description="Gera um plano de migration sem executar alteracoes")
    @app_commands.describe(nick="Nick Minecraft do jogador", reason="Motivo administrativo opcional")
    @app_commands.autocomplete(nick=autocomplete_player)
    async def plan_slash(interaction: discord.Interaction, nick: str, reason: str = "") -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para planejar migrations.")
            return
        await _defer(interaction)
        request = await _resolve_plan_request(interaction, client, audit, nick, reason or None)
        if request:
            await _plan(interaction, client, audit, request)

    @group.command(name="status", description="Consulta o estado de uma migration")
    @app_commands.autocomplete(nick=autocomplete_player)
    async def status_slash(interaction: discord.Interaction, nick: str) -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para consultar migrations.")
            return
        await _defer(interaction)
        await _status_target(interaction, client, audit, nick)

    @group.command(name="execute", description="Cria uma confirmacao para executar a migration planejada de um jogador")
    @app_commands.describe(nick="Nick Minecraft do jogador")
    @app_commands.autocomplete(nick=autocomplete_player)
    async def execute_slash(interaction: discord.Interaction, nick: str) -> None:
        if not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para executar migrations.")
            return
        await _defer(interaction)
        await _execute_target(interaction, client, audit, confirmations, nick)

    @group.command(name="rollback", description="Cria uma confirmacao para rollback de migration")
    @app_commands.autocomplete(nick=autocomplete_player)
    async def rollback_slash(interaction: discord.Interaction, nick: str) -> None:
        if not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para fazer rollback de migrations.")
            return
        await _defer(interaction)
        await _rollback_target(interaction, client, audit, confirmations, nick)

    @group.command(name="confirm", description="Confirma uma execute/rollback pendente")
    @app_commands.autocomplete(migration_id=autocomplete_migration_id)
    async def confirm_slash(interaction: discord.Interaction, action: str, migration_id: str, token: str) -> None:
        if not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para confirmar migrations.")
            return
        await _defer(interaction)
        await _confirm(interaction, client, audit, confirmations, action, migration_id, token)

    bot.tree.add_command(group)
    return client


async def _inspect(target, client: MigrationEngineClient, audit: MigrationAuditStore, request: MigrationInspectRequest) -> None:
    user_id = _user_id(target)
    try:
        result = await client.inspect(request)
    except MigrationEngineError as error:
        audit.record("MIGRATION_INSPECT", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    audit.record(
        "MIGRATION_INSPECT",
        discord_user_id=user_id,
        canonical_uuid=result.identity.canonical_uuid,
        presence=result.presence.value,
        lock_state=result.lock_state.value,
        result="ok",
    )
    await _reply(target, _format_inspection(result))


async def _plan(target, client: MigrationEngineClient, audit: MigrationAuditStore, request: MigrationPlanRequest) -> None:
    await _defer(target)
    user_id = _user_id(target)
    _log_event("migration_plan_request", operator_id=user_id, request=request.payload())
    try:
        result = await client.plan(request)
    except MigrationEngineError as error:
        _log_engine_error("migration_plan_failure", error)
        audit.record("MIGRATION_PLAN", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    except Exception as unexpected:
        logger.exception("Unexpected error generating migration plan")
        audit.record("MIGRATION_PLAN", discord_user_id=user_id, result="failed", error=str(unexpected))
        await _reply(target, "Ocorreu um erro interno ao gerar o plano de migração. Tente novamente.")
        return
    effective_sources = result.sources or request.sources
    if effective_sources and not result.sources:
        result = replace(result, sources=effective_sources, target_discord_user_id=result.target_discord_user_id or (request.target or {}).get("discord_user_id"), dataset_policy=result.dataset_policy or request.dataset_policy)
    _log_event("migration_plan_result", operator_id=user_id, migration_id=result.migration_id, source_count=len(effective_sources), status=result.status, blockers=result.blockers, conflicts=result.conflicts)
    audit.record(
        "MIGRATION_PLAN",
        discord_user_id=user_id,
        canonical_uuid=result.canonical_target,
        migration_id=result.migration_id,
        presence=result.presence.value,
        lock_state=result.lock_state.value,
        result="ok",
    )
    view = _plan_action_view(client, audit, result, user_id)
    if result.conflicts and request.sources:
        view = _DatasetPolicyView(client, audit, request.sources, request.target or {}, request.reason)
    await _reply(target, _format_plan(result), view=view)


async def _status(target, client: MigrationEngineClient, audit: MigrationAuditStore, migration_id: str) -> None:
    user_id = _user_id(target)
    try:
        result = await client.status(migration_id)
    except (ValueError, MigrationEngineError) as error:
        audit.record("MIGRATION_STATUS", discord_user_id=user_id, migration_id=migration_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    audit.record(
        "MIGRATION_STATUS",
        discord_user_id=user_id,
        migration_id=result.migration_id,
        presence=result.presence.value,
        lock_state=result.lock_state.value,
        result=result.state.value,
    )
    await _reply(target, _format_status(result))


async def _status_target(target, client: MigrationEngineClient, audit: MigrationAuditStore, value: str) -> None:
    if not str(value or "").strip():
        await _reply(target, "Informe um jogador/nick ou escolha uma migration pelo autocomplete.")
        return
    if str(value or "").startswith("<@"):
        await _status_by_selector(target, client, audit, MigrationInspectRequest(discord_user_id=_clean_discord_mention(value)))
        return
    request = await _resolve_target(target, client, value, allow_migration_id=True)
    if request:
        if _looks_like_migration_id(value):
            await _status(target, client, audit, value)
        else:
            await _status_by_selector(target, client, audit, request)
        return
    await _status(target, client, audit, value)


async def _status_by_selector(target, client: MigrationEngineClient, audit: MigrationAuditStore, selector: MigrationInspectRequest) -> None:
    user_id = _user_id(target)
    try:
        matches = await client.list_migrations(selector=selector, limit=5)
    except MigrationEngineError as error:
        audit.record("MIGRATION_STATUS", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    if not matches:
        await _reply(target, "Nenhuma migration encontrada para essa identidade.")
        return
    await _status(target, client, audit, matches[0].migration_id)


async def _request_confirmation(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    confirmations: MigrationConfirmationStore,
    action: str,
    migration_id: str,
) -> None:
    user_id = _user_id(target)
    _log_event("migration_confirmation_requested", operator_id=user_id, migration_id=migration_id, action=action)
    event = "MIGRATION_EXECUTE_REQUESTED" if action == "execute" else "MIGRATION_ROLLBACK_REQUESTED"
    try:
        status = await client.status(migration_id)
    except (ValueError, MigrationEngineError) as error:
        audit.record(event, discord_user_id=user_id, migration_id=migration_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    if not status.allows_execute_confirmation():
        audit.record(
            event,
            discord_user_id=user_id,
            migration_id=status.migration_id,
            presence=status.presence.value,
            lock_state=status.lock_state.value,
            result="blocked",
            metadata={"blockers": ", ".join(status.blockers)},
        )
        await _reply(target, _mutation_blocked_message(status))
        return
    if action == "rollback" and not status.rollback_available:
        audit.record(event, discord_user_id=user_id, migration_id=status.migration_id, result="blocked", error="rollback_unavailable")
        await _reply(target, "Rollback indisponivel para esta migration.")
        return
    pending = confirmations.create(action=action, migration_id=status.migration_id, operator_id=user_id)
    audit.record(
        event,
        discord_user_id=user_id,
        migration_id=status.migration_id,
        presence=status.presence.value,
        lock_state=status.lock_state.value,
        result="confirmation_created",
    )
    await _reply(
        target,
        "\n".join(
            [
                f"Confirmacao criada para `{action}`.",
                f"Migration: `{status.migration_id}`",
                "Nada foi executado ainda.",
                "Clique em confirmar para continuar.",
                f"Expira em {confirmations.ttl_seconds}s e so vale para voce.",
            ]
        ),
        view=_ConfirmationView(client, audit, confirmations, action, status.migration_id, user_id, pending.token),
    )


async def _execute_target(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    confirmations: MigrationConfirmationStore,
    value: str,
) -> None:
    if not str(value or "").strip():
        await _reply(target, "Informe o nick do jogador.")
        return
    if _looks_like_migration_id(value):
        await _request_confirmation(target, client, audit, confirmations, "execute", value)
        return
    if not _looks_like_advanced_target(value):
        source_matches = await _search_players_for_execute(client, value)
        if source_matches:
            planned = await _planned_by_sources(client, source_matches)
            if len(planned) == 1:
                await _request_confirmation(target, client, audit, confirmations, "execute", planned[0].migration_id)
                return
            if len(planned) > 1:
                await _reply(target, "Ha mais de uma migration planejada. Escolha explicitamente uma delas:\n" + _format_reference_list(tuple(planned)))
                return
    request = await _resolve_target(target, client, value)
    if not request:
        return
    user_id = _user_id(target)
    try:
        matches = await client.list_migrations(selector=request, state=MigrationState.PLANNED, limit=5)
    except MigrationEngineError as error:
        audit.record("MIGRATION_EXECUTE_REQUESTED", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    if not matches:
        await _reply(target, "Nenhuma migration planejada foi encontrada para esse jogador. Gere um plan primeiro.")
        return
    if len(matches) > 1:
        await _reply(target, "Ha mais de uma migration planejada. Abra o plan/status correto e use o botao dele:\n" + _format_reference_list(matches))
        return
    await _reply(target, f"Migration planejada encontrada: `{matches[0].migration_id}` ({matches[0].label()}). Vou pedir confirmacao agora.")
    await _request_confirmation(target, client, audit, confirmations, "execute", matches[0].migration_id)


async def _search_players_for_execute(client: MigrationEngineClient, value: str) -> tuple[PlayerReference, ...]:
    try:
        return await client.search_players(str(value).strip(), limit=10)
    except MigrationEngineError:
        return ()


async def _planned_by_sources(client: MigrationEngineClient, players: tuple[PlayerReference, ...]) -> list[MigrationReference]:
    found: dict[str, MigrationReference] = {}
    for player in players:
        for source in player.source_list():
            try:
                matches = await client.list_migrations(
                    selector=MigrationInspectRequest(platform=source.platform, external_id=source.external_id),
                    state=MigrationState.PLANNED,
                    limit=10,
                )
            except MigrationEngineError:
                continue
            found.update({item.migration_id: item for item in matches})
    return list(found.values())


async def _rollback_target(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    confirmations: MigrationConfirmationStore,
    value: str,
) -> None:
    if not str(value or "").strip():
        await _reply(target, "Informe um jogador/nick ou escolha uma migration pelo autocomplete.")
        return
    if str(value or "").startswith("<@"):
        await _rollback_by_selector(target, client, audit, confirmations, MigrationInspectRequest(discord_user_id=_clean_discord_mention(value)))
        return
    request = await _resolve_target(target, client, value, allow_migration_id=True)
    if request:
        if _looks_like_migration_id(value):
            await _request_confirmation(target, client, audit, confirmations, "rollback", value)
        else:
            await _rollback_by_selector(target, client, audit, confirmations, request)
        return
    await _request_confirmation(target, client, audit, confirmations, "rollback", value)


async def _rollback_by_selector(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    confirmations: MigrationConfirmationStore,
    selector: MigrationInspectRequest,
) -> None:
    user_id = _user_id(target)
    try:
        matches = await client.list_migrations(selector=selector, state=MigrationState.EXECUTED, rollback_available=True, limit=5)
    except MigrationEngineError as error:
        audit.record("MIGRATION_ROLLBACK_REQUESTED", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    if not matches:
        await _reply(target, "Nenhuma migration executada com rollback disponivel foi encontrada para essa identidade.")
        return
    if len(matches) > 1:
        await _reply(target, "Ha mais de uma migration elegivel. Escolha explicitamente uma delas:\n" + _format_reference_list(matches))
        return
    found = matches[0]
    await _reply(target, f"Migration encontrada para rollback: `{found.migration_id}` ({found.label()}). Vou pedir confirmacao agora.")
    await _request_confirmation(target, client, audit, confirmations, "rollback", found.migration_id)


async def _confirm(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    confirmations: MigrationConfirmationStore,
    action: str,
    migration_id: str,
    token: str,
) -> None:
    action = str(action or "").strip().lower()
    if action not in {"execute", "rollback"}:
        await _reply(target, "Acao invalida. Use `execute` ou `rollback`.")
        return
    user_id = _user_id(target)
    _log_event("migration_confirmation", operator_id=user_id, migration_id=migration_id, action=action)
    confirmed_event = "MIGRATION_EXECUTE_CONFIRMED" if action == "execute" else "MIGRATION_ROLLBACK_CONFIRMED"
    succeeded_event = "MIGRATION_EXECUTE_SUCCEEDED" if action == "execute" else "MIGRATION_ROLLBACK_SUCCEEDED"
    failed_event = "MIGRATION_EXECUTE_FAILED" if action == "execute" else "MIGRATION_ROLLBACK_FAILED"
    try:
        confirmations.consume(token, action=action, migration_id=migration_id, operator_id=user_id)
    except MigrationConfirmationError as error:
        audit.record(confirmed_event, discord_user_id=user_id, migration_id=migration_id, result="blocked", error=str(error))
        await _reply(target, str(error))
        return
    audit.record(confirmed_event, discord_user_id=user_id, migration_id=migration_id, result="ok")
    try:
        if action == "execute":
            _log_event("migration_execute_request", operator_id=user_id, migration_id=migration_id)
            result = await client.execute(migration_id, operator_id=user_id, idempotency_key=str(uuid.uuid4()))
            _log_event("migration_execute_success", operator_id=user_id, migration_id=result.migration_id, result=result.result)
            audit.record(
                succeeded_event,
                discord_user_id=user_id,
                migration_id=result.migration_id,
                presence=result.presence.value,
                lock_state=result.lock_state.value,
                result=result.result,
            )
            await _reply(
                target,
                _format_execution(result.migration_id, result.result, result.migrated_datasets, result.verifications),
                view=_ExecutionActionView(client, audit, confirmations, result.migration_id, user_id, rollback_available=result.rollback_available),
            )
            return
        result = await client.rollback(migration_id, operator_id=user_id, idempotency_key=str(uuid.uuid4()))
        audit.record(
            succeeded_event,
            discord_user_id=user_id,
            migration_id=result.migration_id,
            presence=result.presence.value,
            lock_state=result.lock_state.value,
            result=result.result,
        )
        await _reply(target, _format_execution(result.migration_id, result.result, result.restored_data, result.verifications))
    except MigrationEngineError as error:
        _log_engine_error("migration_execute_failure" if action == "execute" else "migration_rollback_failure", error, migration_id=migration_id)
        audit.record(failed_event, discord_user_id=user_id, migration_id=migration_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))


def _safe_one_click_error_message(error: Exception) -> str:
    if isinstance(error, MigrationEngineError):
        return _error_message(error)
    return "Ocorreu uma falha interna na operacao. Tente novamente; nenhum detalhe interno foi exposto."


def _source_key(source: MigrationSource) -> tuple[str, str, str | None]:
    return (source.platform.casefold(), source.external_id, source.physical_uuid)


def _identity_sources(identity: IdentitySummary) -> tuple[MigrationSource, ...]:
    result = [source for source in identity.sources if source.semantic_type != "CANONICAL_TARGET"]
    for account in (identity.java, identity.bedrock):
        if account:
            source_type = "LINKED_JAVA" if account.platform.casefold() == "java" else "BEDROCK"
            result.append(MigrationSource(account.platform, account.external_id, account.username, source_type=source_type))
    unique = []
    seen = set()
    for source in result:
        key = (source.platform.casefold(), source.external_id)
        if key not in seen and source.semantic_type != "CANONICAL_TARGET":
            seen.add(key)
            unique.append(source)
    return tuple(unique)


def _source_kind_label(source: MigrationSource) -> str:
    return {
        "LEGACY_JAVA": "Java legado",
        "LINKED_JAVA": "Java vinculado",
        "BEDROCK": "Bedrock",
        "UNKNOWN": f"{source.platform.title()} source",
    }.get(source.semantic_type, source.semantic_type.replace("_", " ").title())


def _safe_one_click_metadata(*, operator_id: str, target_discord_id: str, canonical_uuid: str, source: MigrationSource, all_sources: tuple[MigrationSource, ...], migration_id: str | None = None, execution_mode: str | None = None, result: object | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {
        "operator_discord_id": operator_id,
        "target_discord_id": target_discord_id,
        "canonical_uuid": canonical_uuid,
        "pinned_source": source.payload(),
        "source_type": source.source_type,
        "unselected_sources": [item.payload() for item in all_sources if _source_key(item) != _source_key(source)],
    }
    if migration_id:
        metadata["migration_id"] = migration_id
    if execution_mode:
        metadata["execution_mode"] = execution_mode
    if result is not None:
        metadata["result"] = str(result)[:300]
    return metadata


async def _revalidate_pinned_source(client: MigrationEngineClient, *, operator_id: str, canonical_uuid: str, target_discord_id: str, source: MigrationSource) -> IdentitySummary:
    inspected = await client.inspect(MigrationInspectRequest(canonical_uuid=canonical_uuid, discord_user_id=target_discord_id))
    identity = inspected.identity
    if identity.canonical_uuid != canonical_uuid or identity.discord_user_id != target_discord_id:
        raise MigrationEngineRejected(MigrationErrorCode.CONFLICT, "identity changed")
    if _source_key(source) not in {_source_key(item) for item in _identity_sources(identity)}:
        raise MigrationEngineRejected(MigrationErrorCode.CONFLICT, "pinned source changed")
    if inspected.presence is not PresenceState.OFFLINE_CONFIRMED or inspected.lock_state.blocks_mutation or inspected.blockers:
        raise MigrationEngineRejected(MigrationErrorCode.CONFLICT, "preflight no longer safe")
    return identity


async def _restore_paper_safely(target, client, audit, *, operator_id: str, migration_id: str, canonical_uuid: str, target_discord_id: str, source: MigrationSource, all_sources: tuple[MigrationSource, ...], paper_was_stopped: bool) -> None:
    if not paper_was_stopped:
        return
    try:
        await client.maintenance_paper_start(migration_id, operator_id=operator_id)
        await client.maintenance_health()
        await client.maintenance_lock_release(migration_id)
        audit.record("MIGRATION_MAINTENANCE_START", discord_user_id=operator_id, canonical_uuid=canonical_uuid, migration_id=migration_id, result="restored", metadata=_safe_one_click_metadata(operator_id=operator_id, target_discord_id=target_discord_id, canonical_uuid=canonical_uuid, source=source, all_sources=all_sources))
    except Exception:
        logger.exception("Failed to restore Paper after migration failure migration_id=%s", migration_id)
        audit.record("MIGRATION_MAINTENANCE_START", discord_user_id=operator_id, canonical_uuid=canonical_uuid, migration_id=migration_id, result="restore_failed", metadata=_safe_one_click_metadata(operator_id=operator_id, target_discord_id=target_discord_id, canonical_uuid=canonical_uuid, source=source, all_sources=all_sources))


async def _execute_one_click(target, client, audit, *, identity: IdentitySummary, source: MigrationSource, all_sources: tuple[MigrationSource, ...], reason: str | None = None, force_quiescent: bool = False) -> None:
    operator_id = _user_id(target)
    target_discord_id = identity.discord_user_id
    if not target_discord_id:
        await _reply(target, "A identidade nao possui Discord canonico; operacao bloqueada.")
        return
    metadata = _safe_one_click_metadata(operator_id=operator_id, target_discord_id=target_discord_id, canonical_uuid=identity.canonical_uuid, source=source, all_sources=all_sources)
    try:
        await _revalidate_pinned_source(client, operator_id=operator_id, canonical_uuid=identity.canonical_uuid, target_discord_id=target_discord_id, source=source)
        request = MigrationPlanRequest(sources=(source,), target={"discord_user_id": target_discord_id}, dataset_policy=_java_policy((source,)), reason=reason)
        plan = await client.plan(request)
        if plan.canonical_target != identity.canonical_uuid or (plan.status or "").upper() != "READY" or plan.blockers or plan.conflicts:
            raise MigrationEngineRejected(MigrationErrorCode.STALE_PLAN, "plan not ready")
        mode = "QUIESCENT" if force_quiescent else (plan.execution_mode or ("QUIESCENT" if plan.requires_paper_quiescence else "HOT")).upper()
        if mode not in {"HOT", "QUIESCENT"}:
            raise MigrationEngineInvalidResponse("execution_mode invalido")
        audit.record("MIGRATION_PLAN", discord_user_id=operator_id, canonical_uuid=identity.canonical_uuid, migration_id=plan.migration_id, result="ok", metadata={**metadata, "execution_mode": mode})
        paper_was_stopped = False
        proof = None
        if mode == "QUIESCENT" or plan.requires_paper_quiescence:
            await _reply(target, "[2/5] Bloqueando entradas e parando o Paper com seguranca...")
            stopped = await client.maintenance_paper_stop(plan.migration_id, operator_id=operator_id)
            paper_was_stopped = True
            proof = stopped.quiescence_proof
            audit.record("MIGRATION_MAINTENANCE_STOP", discord_user_id=operator_id, canonical_uuid=identity.canonical_uuid, migration_id=plan.migration_id, result="stopped", metadata={**metadata, "execution_mode": "QUIESCENT"})
            if not isinstance(proof, dict) or proof.get("mode") != "PAPER_OFFLINE_CONFIRMED" or proof.get("valid") is not True:
                raise MigrationEngineRejected(MigrationErrorCode.CONFLICT, "invalid quiescence proof")
            await _revalidate_pinned_source(client, operator_id=operator_id, canonical_uuid=identity.canonical_uuid, target_discord_id=target_discord_id, source=source)
            replanned = await client.plan(request)
            if replanned.canonical_target != plan.canonical_target or not replanned.sources or _source_key(replanned.sources[0]) != _source_key(source) or replanned.target_discord_user_id not in {None, target_discord_id} or (replanned.status or "").upper() != "READY" or replanned.blockers or replanned.conflicts:
                raise MigrationEngineRejected(MigrationErrorCode.STALE_PLAN, "re-plan changed")
            plan = replanned
        else:
            await _reply(target, "[2/3] Criando snapshot e migrando sem parar o Paper...")
        key = str(uuid.uuid4())
        audit.record("MIGRATION_EXECUTE_CONFIRMED", discord_user_id=operator_id, canonical_uuid=identity.canonical_uuid, migration_id=plan.migration_id, result="execute", metadata={**metadata, "execution_mode": mode})
        result = await client.execute(plan.migration_id, operator_id=operator_id, idempotency_key=key, execution_mode=mode, quiescence_proof=proof)
        if paper_was_stopped:
            await client.maintenance_paper_start(plan.migration_id, operator_id=operator_id)
            health = await client.maintenance_health()
            if health.health and health.health.get("healthy") is False:
                raise MigrationEngineUnavailable("health check failed")
            await client.maintenance_lock_release(plan.migration_id)
        audit.record("MIGRATION_EXECUTE_SUCCEEDED", discord_user_id=operator_id, canonical_uuid=identity.canonical_uuid, migration_id=result.migration_id, result=result.result, metadata={**metadata, "execution_mode": mode, "snapshot_ref": result.snapshot_ref})
        await _reply(target, _format_execution(result.migration_id, result.result, result.migrated_datasets, result.verifications) + (f"\nSnapshot: `{result.snapshot_ref}`" if result.snapshot_ref else ""))
    except MigrationEngineRejected as error:
        recovery = error.code is MigrationErrorCode.RECOVERY_REQUIRED
        if error.code is MigrationErrorCode.QUIESCENCE_REQUIRED and not force_quiescent:
            await _execute_one_click(target, client, audit, identity=identity, source=source, all_sources=all_sources, reason=reason, force_quiescent=True)
            return
        audit.record("MIGRATION_EXECUTE_FAILED", discord_user_id=operator_id, canonical_uuid=identity.canonical_uuid, migration_id=getattr(locals().get("plan", None), "migration_id", None), result="recovery_required" if recovery else "failed", error=error.code.value, metadata=metadata)
        if not recovery:
            await _restore_paper_safely(target, client, audit, operator_id=operator_id, migration_id=getattr(locals().get("plan", None), "migration_id", "unknown"), canonical_uuid=identity.canonical_uuid, target_discord_id=target_discord_id, source=source, all_sources=all_sources, paper_was_stopped=bool(locals().get("paper_was_stopped", False)))
        message = "RECOVERY_REQUIRED: acao operacional manual necessaria. Paper nao sera iniciado automaticamente." if recovery else _error_message(error)
        await _reply(target, message)
    except (MigrationEngineError, Exception) as error:
        logger.exception("Unexpected one-click migration failure")
        audit.record("MIGRATION_EXECUTE_FAILED", discord_user_id=operator_id, canonical_uuid=identity.canonical_uuid, result="failed", error=type(error).__name__, metadata=metadata)
        await _restore_paper_safely(target, client, audit, operator_id=operator_id, migration_id=getattr(locals().get("plan", None), "migration_id", "unknown"), canonical_uuid=identity.canonical_uuid, target_discord_id=target_discord_id, source=source, all_sources=all_sources, paper_was_stopped=bool(locals().get("paper_was_stopped", False)))
        await _reply(target, _safe_one_click_error_message(error))


async def _one_click_migrate(target, client, audit, value: str | None = None, reason: str | None = None) -> None:
    operator_id = _user_id(target)
    query = str(value or operator_id).strip()
    try:
        if query == operator_id and not value:
            inspection = await client.inspect(MigrationInspectRequest(discord_user_id=operator_id))
        else:
            request = _request_from_target(query)
            if query.startswith("<@"):
                request = MigrationInspectRequest(discord_user_id=_clean_discord_mention(query))
            if request.player_name:
                matches = await client.search_players(request.player_name, limit=10)
                canonical_ids = {item.canonical_uuid for item in matches if item.canonical_uuid}
                discord_ids = {item.discord_user_id for item in matches if item.discord_user_id}
                if len(matches) != 1 and not (len(canonical_ids) == 1 and len(discord_ids) <= 1):
                    await _reply(target, "A busca nao identificou uma identidade canonica unica. Operacao bloqueada.")
                    return
                match = matches[0]
                request = MigrationInspectRequest(canonical_uuid=next(iter(canonical_ids)), discord_user_id=next(iter(discord_ids), None))
            inspection = await client.inspect(request)
        if not inspection.identity.discord_user_id:
            await _reply(target, "A identidade nao possui Discord canonico; operacao bloqueada.")
            return
        if not value and inspection.identity.discord_user_id != operator_id:
            await _reply(target, "A identidade retornada nao pertence ao operador; operacao bloqueada.")
            return
        sources = _identity_sources(inspection.identity)
        if not sources:
            await _reply(target, "Nenhuma source real foi encontrada para esta identidade.")
            return
        if len(sources) > 1:
            await _reply(target, "Escolha explicitamente a source que sera a fonte da verdade.", view=_OneClickSourceView(operator_id, client, audit, inspection.identity, sources, reason))
            return
        await _execute_one_click(target, client, audit, identity=inspection.identity, source=sources[0], all_sources=sources, reason=reason)
    except MigrationEngineError as error:
        audit.record("MIGRATION_PLAN", discord_user_id=operator_id, result="failed", error=error.code.value)
        await _reply(target, _error_message(error))


def _request_from_target(target: str) -> MigrationInspectRequest:
    target = str(target or "").strip()
    if target.casefold().startswith("nick:"):
        return MigrationInspectRequest(player_name=target.split(":", 1)[1].strip())
    if target.casefold().startswith("player:"):
        return MigrationInspectRequest(canonical_uuid=target.split(":", 1)[1].strip())
    if target.casefold().startswith("java:"):
        return MigrationInspectRequest(platform="java", external_id=target.split(":", 1)[1].strip())
    if target.casefold().startswith("bedrock:"):
        return MigrationInspectRequest(platform="bedrock", external_id=target.split(":", 1)[1].strip())
    try:
        uuid.UUID(target)
        return MigrationInspectRequest(canonical_uuid=target.lower())
    except ValueError:
        pass
    if target.isdecimal():
        return MigrationInspectRequest(discord_user_id=target)
    return MigrationInspectRequest(player_name=target)


def _plan_request_from_target(target: str, reason: str | None) -> MigrationPlanRequest:
    request = _request_from_target(target)
    return _plan_request_from_request(request, reason)


def _plan_request_from_request(request: MigrationInspectRequest, reason: str | None) -> MigrationPlanRequest:
    return MigrationPlanRequest(
        discord_user_id=request.discord_user_id,
        canonical_uuid=request.canonical_uuid,
        platform=request.platform,
        external_id=request.external_id,
        player_name=request.player_name,
        reason=reason,
    )


async def _resolve_plan_request(target, client: MigrationEngineClient, audit: MigrationAuditStore, value: str, reason: str | None) -> MigrationPlanRequest | None:
    value = str(value or "").strip()
    request = _request_from_target(value)
    if not request.player_name:
        return _plan_request_from_request(request, reason)
    _log_event("migration_player_search", operator_id=_user_id(target), query=request.player_name)
    try:
        matches = await client.search_players(request.player_name, limit=10)
    except MigrationEngineNotConfigured:
        return _plan_request_from_request(request, reason)
    except MigrationEngineError as error:
        _log_engine_error("migration_player_search_failure", error)
        await _reply(target, _error_message(error))
        return None
    if not matches:
        await _reply(target, f"Nenhum jogador encontrado parecido com `{request.player_name}`.")
        return None
    if len(matches) > 1:
        sources = tuple(source for match in matches for source in match.source_list())
        unique_sources = tuple(dict.fromkeys(sources))
        discord_ids = tuple(dict.fromkeys(match.discord_user_id for match in matches if match.discord_user_id))
        target_id = discord_ids[0] if len(discord_ids) == 1 else None
        if not target_id:
            await _reply(target, "As contas encontradas nao possuem um Discord alvo unico. Informe o Discord explicitamente.")
            return None
        _log_event("migration_source_selection", operator_id=_user_id(target), query=request.player_name, sources=unique_sources, target_discord_user_id=target_id)
        await _reply(
            target,
            _format_source_selection(request.player_name, unique_sources, target_id),
            view=_SourceSelectionView(client, audit, reason, request.player_name, unique_sources, target_id),
        )
        return None
    match = matches[0]
    sources = match.source_list()
    if len(sources) > 1 and match.discord_user_id:
        await _reply(target, _format_source_selection(match.player_name, sources, match.discord_user_id), view=_SourceSelectionView(client, audit, reason, match.player_name, sources, match.discord_user_id))
        return None
    if match.discord_user_id and sources:
        return _multi_source_request(sources, match.discord_user_id, _java_policy(sources), reason)
    if match.canonical_uuid:
        return MigrationPlanRequest(canonical_uuid=match.canonical_uuid, reason=reason)
    await _reply(target, "O candidato nao trouxe uma identidade Discord ou source utilizavel; plano nao criado.")
    return None


def _multi_source_request(sources: tuple[MigrationSource, ...], discord_user_id: str, policy: dict[str, object], reason: str | None) -> MigrationPlanRequest:
    return MigrationPlanRequest(
        sources=sources,
        target={"discord_user_id": str(discord_user_id)},
        dataset_policy=dict(policy),
        reason=reason,
    )


def _java_policy(sources: tuple[MigrationSource, ...]) -> dict[str, str]:
    preferred = "java" if any(source.platform == "java" for source in sources) else sources[0].platform
    return {dataset: preferred for dataset in ("playerdata", "advancements", "stats")}


def _policy_for_platform(platform: str) -> dict[str, str]:
    return {dataset: platform for dataset in ("playerdata", "advancements", "stats")}


def _replace_target_policy(source: MigrationSource) -> dict[str, object]:
    return {
        "strategy": "REPLACE_TARGET",
        "source": {"platform": source.platform, "external_id": source.external_id},
        "confirmed_replace_target": True,
        "global_preserve": ["simplelogin"],
    }


async def _resolve_target(
    target,
    client: MigrationEngineClient,
    value: str,
    *,
    allow_migration_id: bool = False,
) -> MigrationInspectRequest | None:
    value = str(value or "").strip()
    if not value:
        await _reply(target, "Informe o nick do jogador, @Discord ou um identificador avancado.")
        return None
    if allow_migration_id and _looks_like_migration_id(value):
        return MigrationInspectRequest()
    request = _request_from_target(value)
    if not request.player_name:
        return request
    try:
        matches = await client.search_players(request.player_name, limit=5)
    except MigrationEngineNotConfigured:
        return request
    except MigrationEngineError as error:
        await _reply(target, _error_message(error))
        return None
    if not matches:
        await _reply(target, f"Nenhum jogador encontrado parecido com `{request.player_name}`.")
        return None
    if len(matches) > 1:
        await _reply(target, "Encontrei mais de um jogador parecido. Escolha pelo autocomplete:\n" + _format_player_list(matches))
        return None
    return MigrationInspectRequest(canonical_uuid=matches[0].canonical_uuid)


def _is_staff(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and (permissions.manage_guild or permissions.administrator)) or _has_migration_role(user)


def _is_admin(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and permissions.administrator) or any(
        name in {"migration-admin", "migration_admin", "migration admin"}
        for name in _role_names(user)
    )


def _role_names(user) -> tuple[str, ...]:
    return tuple(str(getattr(role, "name", "")).casefold().strip() for role in (getattr(user, "roles", None) or ()))


def _has_migration_role(user) -> bool:
    return any(name in {"staff", "migration-admin", "migration_admin", "migration admin"} for name in _role_names(user))


def _user_id(target) -> str:
    user = target.user if _is_interaction_like(target) else target.author
    return str(user.id)


async def _reply(target, message: str, *, view: discord.ui.View | None = None) -> None:
    message = message[:1900]
    kwargs = {"ephemeral": True}
    if view is not None:
        kwargs["view"] = view
    if _is_interaction_like(target):
        if target.response.is_done():
            await target.followup.send(message, **kwargs)
        else:
            await target.response.send_message(message, **kwargs)
    else:
        kwargs = {"view": view} if view is not None else {}
        await target.send(message, **kwargs)


def _is_interaction_like(target) -> bool:
    return hasattr(target, "user") and hasattr(target, "response") and hasattr(target, "followup")


async def _defer(interaction) -> None:
    if _is_interaction_like(interaction) and hasattr(interaction.response, "defer") and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


def _format_inspection(result) -> str:
    identity: IdentitySummary = result.identity
    lines = [
        "Migration inspect",
        f"Identidade canonica: `{identity.canonical_uuid}`",
        f"Discord: `{identity.discord_user_id or 'desconhecido'}`",
        f"Nome canonico: `{identity.canonical_name or 'desconhecido'}`",
        f"Java: {_account(identity.java)}",
        f"Bedrock: {_account(identity.bedrock)}",
        "Fontes anexadas:",
        f"Presence: `{result.presence.value}`",
        f"Gateway Lock: `{result.lock_state.value}`",
        f"Dados encontrados: {_csv(result.detected_data)}",
        f"Warnings: {_csv(result.warnings)}",
        f"Blockers: {_csv(result.blockers)}",
    ]
    sources = _identity_sources(identity)
    lines[7:7] = [f"- {_source_kind_label(source)}: `{source.username or source.external_id}` — `{source.external_id}`" for source in sources] or ["- `nenhuma`"]
    return "\n".join(lines)


def _format_plan(plan: MigrationPlan) -> str:
    lines = [
        "Migration PLAN gerado. NADA foi executado.",
        f"migration_id: `{plan.migration_id}`",
        f"Alvo canonico: `{plan.canonical_target}`",
        f"Presence: `{plan.presence.value}`",
        f"Gateway Lock: `{plan.lock_state.value}`",
        f"Rollback: `{'disponivel' if plan.rollback_available else 'indisponivel'}`",
        f"Modo: `{plan.execution_mode or 'desconhecido'}`",
        f"Operacoes: {_csv(tuple(f'{op.dataset}:{op.action}' if op.dataset else op.action for op in plan.operations))}",
        f"Datasets: {_csv(plan.affected_datasets)}",
        f"Conflitos: {_csv(plan.conflicts)}",
        f"Warnings: {_csv(plan.warnings)}",
        f"Blockers: {_csv(plan.blockers)}",
    ]
    if plan.execution_mode == "HOT":
        lines.append("⚡ Pode ser migrado sem reiniciar o servidor.")
    elif plan.execution_mode == "QUIESCENT":
        lines.append("🛠 Esta migração exige janela de manutenção.")
    if plan.sources:
        lines.insert(3, "Sources: " + ", ".join(f"{source.platform}:{source.username or source.external_id}" for source in plan.sources))
    if plan.target_discord_user_id:
        lines.insert(4, f"Discord alvo: `{plan.target_discord_user_id}`")
    if plan.dataset_policy:
        lines.insert(5, "Policy: " + ", ".join(f"{key}={value}" for key, value in plan.dataset_policy.items()))
    if plan.status:
        lines.insert(3, f"Status: `{plan.status}`")
    return "\n".join(lines)


def _format_status(status: MigrationStatus) -> str:
    return "\n".join(
        [
            f"Migration `{status.migration_id}`",
            f"Estado: `{status.state.value}`",
            f"Presence: `{status.presence.value}`",
            f"Gateway Lock: `{status.lock_state.value}`",
            f"Verificacao: `{status.verification_state or 'desconhecida'}`",
            f"Rollback: `{'disponivel' if status.rollback_available else 'indisponivel'}`",
            f"Warnings: {_csv(status.warnings)}",
            f"Blockers: {_csv(status.blockers)}",
        ]
    )


def _format_execution(migration_id: str, result: str, datasets: tuple[str, ...], verifications: tuple[str, ...]) -> str:
    return "\n".join(
        [
            f"Migration `{migration_id}` confirmou resposta do engine.",
            f"Resultado: `{result}`",
            f"Dados: {_csv(datasets)}",
            f"Verificacoes: {_csv(verifications)}",
        ]
    )


def _format_reference_list(matches: tuple[MigrationReference, ...]) -> str:
    return "\n".join(f"- `{item.migration_id}` {item.label()}" for item in matches[:10])


def _format_player_list(matches: tuple[PlayerReference, ...]) -> str:
    return "\n".join(f"- `{item.player_name}` {item.label()}" for item in matches[:10])


def _format_source_selection(player_name: str, sources: tuple[MigrationSource, ...], target_discord_id: str) -> str:
    lines = [f"Contas Minecraft encontradas para `{player_name}`:", f"Discord alvo: `{target_discord_id}`", ""]
    for source in sources:
        if source.semantic_type == "CANONICAL_TARGET":
            continue
        name = source.username or source.external_id
        lines.append(f"{_source_kind_label(source)} — {name}\n`{source.external_id}`")
    lines.append("\nEscolha explicitamente as sources que serao migradas.")
    return "\n".join(lines)


def _format_source_selection_legacy(player_name: str, sources: tuple[MigrationSource, ...], target_discord_id: str) -> str:
    lines = [f"Contas Minecraft encontradas para `{player_name}`:", f"Discord alvo: `{target_discord_id}`", ""]
    for source in sources:
        icon = "☕" if source.platform == "java" else "📱" if source.platform == "bedrock" else "🔹"
        tag = f" ({source.source_type})" if getattr(source, "source_type", None) else ""
        name = source.username or source.external_id
        lines.append(f"{icon} {source.platform.title()} — {name}{tag}\n`{source.external_id}`")
    lines.append("\nEscolha explicitamente as sources que serão migradas.")
    return "\n".join(lines)


def _plan_action_view(client, audit, plan: MigrationPlan, operator_id: str) -> discord.ui.View | None:
    if (plan.status or "").upper() != "READY" or plan.blockers or plan.conflicts:
        logger.warning("migration_execute_button_suppressed migration_id=%s reason=blocked_or_conflicted_plan", plan.migration_id)
        return None
    if len(plan.sources or plan.source_identities) >= 2 and not _safe_multi_source_plan(plan):
        logger.warning("migration_execute_button_suppressed migration_id=%s reason=unsafe_multi_source_plan", plan.migration_id)
        return None
    return _PlanActionView(client, audit, confirmations=None, plan=plan, operator_id=operator_id)


def _safe_multi_source_plan(plan: MigrationPlan) -> bool:
    actions = {operation.action.upper() for operation in plan.operations}
    required = {"CREATE_CANONICAL_IDENTITY", "ATTACH_JAVA_IDENTITY", "ATTACH_BEDROCK_IDENTITY", "MOVE_FILE", "ARCHIVE_FILE"}
    policy = plan.dataset_policy or {}
    if policy.get("strategy") == "REPLACE_TARGET":
        return (
            (plan.status or "").upper() == "READY"
            and not plan.blockers
            and not plan.conflicts
            and plan.rollback_available
        )
    return (
        (plan.status or "").upper() == "READY"
        and not plan.blockers
        and not plan.conflicts
        and len(plan.sources) >= 2
        and plan.canonical_target.lower() not in {value.lower() for source in plan.sources for value in (source.external_id, source.physical_uuid) if value}
        and bool(plan.target_discord_user_id)
        and all(policy.get(dataset) in {source.platform for source in plan.sources} for dataset in ("playerdata", "advancements", "stats"))
        and plan.rollback_available
        and required.issubset(actions)
        and "REWRITE_UUID" not in actions
    )


def _mutation_blocked_message(status: MigrationStatus) -> str:
    if status.presence is PresenceState.ONLINE:
        return "Execute/Rollback abortado: jogador ONLINE."
    if status.presence is PresenceState.UNKNOWN:
        return "Execute/Rollback abortado: presenca UNKNOWN nao e tratada como offline."
    if status.lock_state.blocks_mutation:
        return f"Execute/Rollback abortado: Gateway Lock `{status.lock_state.value}`."
    if status.blockers:
        return f"Execute/Rollback abortado por blockers: {_csv(status.blockers)}"
    return "Execute/Rollback abortado por estado inseguro."


def _error_message(error: Exception) -> str:
    if isinstance(error, MigrationEngineNotConfigured):
        return "Migration indisponivel: configure MIGRATION_ENGINE_BASE_URL e MIGRATION_ENGINE_TOKEN."
    if isinstance(error, MigrationEngineAuthError):
        return "Migration Engine recusou autenticacao. Verifique o token configurado."
    if isinstance(error, MigrationEngineUnavailable):
        return "Migration Engine indisponivel ou demorou demais para responder."
    if isinstance(error, MigrationEngineInvalidResponse):
        return "Migration Engine retornou uma resposta invalida/incompleta."
    if isinstance(error, MigrationEngineRejected):
        details = [f"Motivo: {str(error)}", f"Código: `{error.code.value}`"]
        if error.blockers:
            details.append("Blockers: " + _csv(error.blockers))
        if error.conflicts:
            details.append("Conflitos: " + _csv(error.conflicts))
        details.append("Nenhum dado foi alterado.")
        mapped = {
            MigrationErrorCode.PLAYER_ONLINE: "Jogador online; operacao recusada.",
            MigrationErrorCode.PRESENCE_UNKNOWN: "Presenca UNKNOWN; operacao recusada.",
            MigrationErrorCode.LOCK_ACTIVE: "Gateway Lock ativo; operacao recusada.",
            MigrationErrorCode.NOT_FOUND: "Migration nao encontrada.",
            MigrationErrorCode.ALREADY_EXECUTED: "Migration ja executada.",
            MigrationErrorCode.ROLLBACK_UNAVAILABLE: "Rollback indisponivel.",
            MigrationErrorCode.CONFLICT: "Conflito detectado pelo Migration Engine.",
            MigrationErrorCode.STALE_PLAN: "O plano ficou desatualizado; gere uma nova operacao.",
            MigrationErrorCode.QUIESCENCE_REQUIRED: "O Engine exige uma janela segura de manutencao.",
            MigrationErrorCode.RECOVERY_REQUIRED: "O Engine exige recuperacao operacional manual.",
        }
        details[0] = f"Motivo: {mapped.get(error.code, 'Operacao recusada pelo Migration Engine.')}"
        return "\n".join(details)
    return "Ocorreu uma falha interna na operacao. Tente novamente; nenhum detalhe interno foi exposto."


def _account(account) -> str:
    if not account:
        return "`ausente`"
    suffix = f" ({account.username})" if account.username else ""
    return f"`{account.platform}:{account.external_id}`{suffix}"


def _csv(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "`nenhum`"


async def _migration_id_autocomplete(
    client: MigrationEngineClient,
    current: str,
    *,
    state: MigrationState | None = None,
    rollback_available: bool | None = None,
) -> list[app_commands.Choice[str]]:
    try:
        matches = await asyncio.wait_for(
            client.list_migrations(query=current, state=state, rollback_available=rollback_available, limit=10),
            timeout=1.5,
        )
    except (MigrationEngineError, asyncio.TimeoutError):
        return []
    return [app_commands.Choice(name=item.label()[:100], value=item.migration_id) for item in matches[:10]]


async def _player_autocomplete(client: MigrationEngineClient, current: str) -> list[app_commands.Choice[str]]:
    text = str(current or "").strip()
    if not text or _looks_like_advanced_target(text):
        return []
    try:
        matches = await asyncio.wait_for(client.search_players(text, limit=10), timeout=1.5)
    except (MigrationEngineError, asyncio.TimeoutError):
        return []
    return [
        app_commands.Choice(
            name=item.label()[:100],
            value=f"player:{item.canonical_uuid}" if item.canonical_uuid else f"nick:{item.player_name}",
        )
        for item in matches[:10]
    ]


def _looks_like_advanced_target(value: str) -> bool:
    value = str(value or "").strip()
    return (
        value.casefold().startswith(("player:", "nick:", "java:", "bedrock:", "mig_"))
        or value.casefold().startswith("mig-")
        or value.startswith("<@")
        or value.isdecimal()
    )


def _looks_like_migration_id(value: str) -> bool:
    return str(value or "").strip().casefold().startswith(("mig_", "mig-"))


def _clean_discord_mention(value: str) -> str:
    return str(value or "").strip().removeprefix("<@").removeprefix("!").removesuffix(">")


def _log_event(event: str, **fields) -> None:
    safe = {key: value for key, value in fields.items() if key not in {"token", "authorization", "headers"}}
    logger.info("migration_event=%s data=%s", event, safe)


def _log_engine_error(event: str, error: MigrationEngineError, **fields) -> None:
    _log_event(event, **fields, error_code=getattr(error.code, "value", str(error.code)), http_status=error.status, message=str(error))


class _DynamicSourceButton(discord.ui.Button):
    def __init__(self, source: MigrationSource, parent_view: "_SourceSelectionView", label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.source = source
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view._select_source(interaction, self.source)


class _DynamicCompareButton(discord.ui.Button):
    def __init__(self, parent_view: "_SourceSelectionView"):
        super().__init__(label="📊 Comparar progresso", style=discord.ButtonStyle.secondary, row=1)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await _show_comparison_preview(
            interaction,
            self.parent_view.client,
            self.parent_view.audit,
            self.parent_view.sources,
            self.parent_view.target_discord_id,
            self.parent_view.player_name,
            self.parent_view.reason,
        )


class _SourceSelectionView(discord.ui.View):
    def __init__(self, client, audit, reason, player_name, sources, target_discord_id):
        super().__init__(timeout=120)
        self.client = client
        self.audit = audit
        self.reason = reason
        self.player_name = player_name
        self.sources = tuple(source for source in sources if source.semantic_type != "CANONICAL_TARGET")
        self.target_discord_id = str(target_discord_id)

        platform_counts = Counter(s.platform for s in self.sources)
        if any(count > 1 for count in platform_counts.values()):
            self.clear_items()
            for source in self.sources:
                tag = f" ({source.source_type})" if getattr(source, "source_type", None) else ""
                name = source.username or source.external_id[:8]
                btn_label = f"Usar {source.platform.title()} — {name}{tag}"
                if len(btn_label) > 80:
                    btn_label = btn_label[:77] + "..."
                self.add_item(_DynamicSourceButton(source, self, btn_label))
            self.add_item(_DynamicCompareButton(self))

    async def _select_source(self, interaction: discord.Interaction, selected: MigrationSource) -> None:
        for child in self.children:
            child.disabled = True
        view = _ReplaceTargetConfirmationView(
            self.client,
            self.audit,
            selected,
            self.target_discord_id,
            self.player_name,
            self.reason,
            str(interaction.user.id),
            all_sources=self.sources,
        )
        _log_event("migration_source_selected", operator_id=str(interaction.user.id), player_name=self.player_name, platforms=(selected.platform,), target_discord_user_id=self.target_discord_id)
        await _edit_or_reply(interaction, view.render_content(), view)

    @discord.ui.button(label="Usar progresso Java", style=discord.ButtonStyle.secondary)
    async def java_button(self, interaction, button):
        selected = next((source for source in self.sources if source.platform == "java"), None)
        if not selected:
            await _reply(interaction, "Source Java não está disponível.")
            return
        await self._select_source(interaction, selected)

    @discord.ui.button(label="Usar progresso Bedrock", style=discord.ButtonStyle.secondary)
    async def bedrock_button(self, interaction, button):
        selected = next((source for source in self.sources if source.platform == "bedrock"), None)
        if not selected:
            await _reply(interaction, "Source Bedrock não está disponível.")
            return
        await self._select_source(interaction, selected)

    @discord.ui.button(label="📊 Comparar progresso", style=discord.ButtonStyle.secondary, row=1)
    async def compare_button(self, interaction, button):
        await _show_comparison_preview(
            interaction,
            self.client,
            self.audit,
            self.sources,
            self.target_discord_id,
            self.player_name,
            self.reason,
        )


class _DatasetPolicyView(discord.ui.View):
    def __init__(self, client, audit, sources, target, reason):
        super().__init__(timeout=120)
        self.client = client
        self.audit = audit
        self.sources = tuple(source for source in sources if source.semantic_type != "CANONICAL_TARGET")
        self.target = dict(target)
        self.reason = reason

    async def _choose(self, interaction, platform):
        for child in self.children:
            child.disabled = True
        selected = next((source for source in self.sources if source.platform == platform), None)
        if not selected:
            await _reply(interaction, f"Source {platform} não está disponível.")
            return
        view = _ReplaceTargetConfirmationView(
            self.client,
            self.audit,
            selected,
            self.target.get("discord_user_id", ""),
            "jogador",
            self.reason,
            str(interaction.user.id),
            all_sources=self.sources,
        )
        policy = _replace_target_policy(selected)
        _log_event("migration_dataset_policy_selected", operator_id=str(interaction.user.id), platforms=tuple(source.platform for source in self.sources), policy=policy)
        await _edit_or_reply(interaction, view.render_content(), view)

    @discord.ui.button(label="Usar progresso Java", style=discord.ButtonStyle.primary)
    async def java_button(self, interaction, button):
        await self._choose(interaction, "java")

    @discord.ui.button(label="Usar progresso Bedrock", style=discord.ButtonStyle.secondary)
    async def bedrock_button(self, interaction, button):
        await self._choose(interaction, "bedrock")

    @discord.ui.button(label="📊 Comparar progresso", style=discord.ButtonStyle.secondary)
    async def compare_button(self, interaction, button):
        await _show_comparison_preview(
            interaction,
            self.client,
            self.audit,
            self.sources,
            self.target.get("discord_user_id", ""),
            "jogador",
            self.reason,
        )


class _ReplaceTargetConfirmationView(discord.ui.View):
    def __init__(
        self,
        client: MigrationEngineClient,
        audit: MigrationAuditStore,
        source: MigrationSource,
        target_discord_id: str,
        player_name: str,
        reason: str | None,
        operator_id: str,
        all_sources: tuple[MigrationSource, ...] | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self.client = client
        self.audit = audit
        self.source = source
        self.target_discord_id = str(target_discord_id)
        self.player_name = str(player_name)
        self.reason = reason
        self.operator_id = str(operator_id)
        self.all_sources = all_sources or (source,)
        self.expires_at = datetime.now(UTC) + timedelta(seconds=180)

    def render_content(self) -> str:
        label = f"{self.source.platform}:{self.source.username or self.source.external_id}"
        return "\n".join([
            f"Fonte selecionada: `{label}`",
            "",
            "⚠️ O progresso atual da conta unificada será substituído.",
            "",
            "Um snapshot será criado antes da migração e poderá ser usado para rollback.",
        ])

    @discord.ui.button(label="Substituir e migrar", style=discord.ButtonStyle.danger)
    async def replace_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._allowed(interaction, admin_required=True):
            return
        for child in self.children:
            child.disabled = True
        request = _multi_source_request(self.all_sources, self.target_discord_id, _replace_target_policy(self.source), self.reason)
        _log_event("migration_replace_target_confirmed", operator_id=str(interaction.user.id), platform=self.source.platform, source=self.source.external_id, target_discord_user_id=self.target_discord_id)
        await _plan(interaction, self.client, self.audit, request)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._allowed(interaction):
            return
        for child in self.children:
            child.disabled = True
        await _reply(interaction, "Migração cancelada. Nenhum dado foi alterado.")

    async def _allowed(self, interaction: discord.Interaction, *, admin_required: bool = False) -> bool:
        if str(interaction.user.id) != self.operator_id:
            await _reply(interaction, "Este componente pertence a outro operador.")
            return False
        if datetime.now(UTC) >= self.expires_at:
            await _reply(interaction, "Este componente expirou. Gere a acao novamente.")
            return False
        if admin_required and not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao administrativa para esta acao.")
            return False
        if not admin_required and not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para esta acao.")
            return False
        return True


async def _edit_or_reply(interaction, content: str, view: discord.ui.View) -> None:
    if hasattr(interaction, "response") and not interaction.response.is_done():
        try:
            await interaction.response.edit_message(content=content, view=view)
            return
        except (AttributeError, discord.InteractionResponded):
            pass
    if hasattr(interaction, "message") and hasattr(interaction.message, "edit"):
        try:
            await interaction.message.edit(content=content, view=view)
            return
        except Exception:
            pass
    await _reply(interaction, content, view=view)


def _render_preview_page(comparison: ComparisonSummary, page: int, player_name: str, target_discord_id: str) -> str:
    if page == 1:
        return _render_inventory_page(comparison, player_name)
    if page == 2:
        return _render_advancements_stats_page(comparison, player_name)
    return _render_summary_page(comparison, player_name, target_discord_id)


def _render_summary_page(comparison: ComparisonSummary, player_name: str, target_discord_id: str) -> str:
    lines = [
        "📊 **PREVIEW COMPARATIVO DE PROGRESSO** (Aba 1/3: Resumo)",
        f"👤 Jogador: `{player_name}` | Discord: <@{target_discord_id}>",
        "",
        f"💡 **{comparison.recommendation.headline}**",
    ]
    for reason in comparison.recommendation.reasons:
        lines.append(f"  • {reason}")
    lines.append("")

    for prof in (comparison.java_profile, comparison.bedrock_profile):
        if not prof:
            continue
        icon = "☕" if prof.platform == "java" else "📱"
        name = prof.username or prof.external_id
        source = MigrationSource(prof.platform, prof.external_id, prof.username, source_type=prof.source_type)
        lines.append(f"{icon} **{_source_kind_label(source)}** (`{name}`):")

        if not prof.playerdata.available:
            lines.append(f"  • Playerdata: ⚠️ *indisponível* ({prof.playerdata.error_message or 'sem dados'})")
        else:
            inv = prof.playerdata.inventory
            ec = prof.playerdata.ender_chest
            xp = format_xp(prof.playerdata.xp_level, prof.playerdata.xp_total)
            dim = format_dimension(prof.playerdata.dimension)
            lines.append(f"  • Playerdata: {xp} | Dimensão: {dim}")
            lines.append(f"  • Inventário: {inv.occupied_slots}/36 slots ({inv.total_items} itens)")
            ec_desc = f"{ec.occupied_slots}/27 slots ({ec.total_items} itens)" if ec.occupied_slots > 0 else "vazio"
            lines.append(f"  • Ender Chest: {ec_desc}")

        if not prof.advancements.available:
            lines.append("  • Conquistas: ⚠️ *indisponível*")
        else:
            adv_count = prof.advancements.total_completed if prof.advancements.total_completed is not None else 0
            lines.append(f"  • Conquistas: {adv_count} concluídas")

        if not prof.stats.available:
            lines.append("  • Estatísticas: ⚠️ *indisponível*")
        else:
            pt = format_playtime(prof.stats.play_time_seconds)
            deaths = prof.stats.deaths if prof.stats.deaths is not None else "indisponível"
            kills = prof.stats.mob_kills if prof.stats.mob_kills is not None else "indisponível"
            lines.append(f"  • Tempo de jogo: {pt} | Mortes: {deaths} | Mobs derrotados: {kills}")
        lines.append("")

    lines.append("Use as abas para inspecionar inventário e stats, ou selecione a política desejada:")
    return "\n".join(lines)


def _render_inventory_page(comparison: ComparisonSummary, player_name: str) -> str:
    lines = [
        "🎒 **PREVIEW DE INVENTÁRIO & EQUIPAMENTOS** (Aba 2/3)",
        f"👤 Jogador: `{player_name}`",
        "",
    ]
    for prof in (comparison.java_profile, comparison.bedrock_profile):
        if not prof:
            continue
        icon = "☕" if prof.platform == "java" else "📱"
        name = prof.username or prof.external_id
        lines.append(f"{icon} **{prof.platform.title()}** (`{name}`):")

        if not prof.playerdata.available:
            lines.append(f"  • Inventário indisponível ({prof.playerdata.error_message or 'sem dados'}).")
            lines.append("")
            continue

        pdata = prof.playerdata
        eq = pdata.equipment
        lines.append("  🛡️ **Equipamentos:**")
        lines.append(f"    - Mão Principal: {format_equipment_item(eq.mainhand)}")
        lines.append(f"    - Secundária: {format_equipment_item(eq.offhand)}")
        armor_parts = []
        if eq.head:
            armor_parts.append(f"Elmo: {format_equipment_item(eq.head)}")
        if eq.chest:
            armor_parts.append(f"Peitoral: {format_equipment_item(eq.chest)}")
        if eq.legs:
            armor_parts.append(f"Calça: {format_equipment_item(eq.legs)}")
        if eq.feet:
            armor_parts.append(f"Botas: {format_equipment_item(eq.feet)}")
        lines.append(f"    - Armadura: {', '.join(armor_parts) if armor_parts else 'nenhuma'}")

        rel_inv = filter_relevant_items(pdata.inventory.items)
        lines.append(f"  💎 **Itens Relevantes no Inventário ({len(rel_inv)}):**")
        if rel_inv:
            for item in rel_inv[:5]:
                ench = f" ✨ ({', '.join(item.enchantments)})" if item.enchantments else ""
                lines.append(f"    • {item.count}x {format_item_name(item.id)}{ench}")
            if len(rel_inv) > 5:
                lines.append(f"    • ...e mais {len(rel_inv) - 5} itens")
        else:
            lines.append("    • *nenhum item valioso detectado*")

        rel_ec = filter_relevant_items(pdata.ender_chest.items)
        lines.append(f"  🔮 **Ender Chest ({pdata.ender_chest.occupied_slots} slots / {pdata.ender_chest.total_items} itens):**")
        if rel_ec:
            for item in rel_ec[:4]:
                ench = f" ✨ ({', '.join(item.enchantments)})" if item.enchantments else ""
                lines.append(f"    • {item.count}x {format_item_name(item.id)}{ench}")
            if len(rel_ec) > 4:
                lines.append(f"    • ...e mais {len(rel_ec) - 4} itens")
        elif pdata.ender_chest.occupied_slots > 0:
            lines.append(f"    • {pdata.ender_chest.occupied_slots} slots ocupados (itens comuns)")
        else:
            lines.append("    • *vazio*")
        lines.append("")

    return "\n".join(lines)


def _render_advancements_stats_page(comparison: ComparisonSummary, player_name: str) -> str:
    lines = [
        "🏆 **CONQUISTAS & ESTATÍSTICAS** (Aba 3/3)",
        f"👤 Jogador: `{player_name}`",
        "",
    ]
    for prof in (comparison.java_profile, comparison.bedrock_profile):
        if not prof:
            continue
        icon = "☕" if prof.platform == "java" else "📱"
        name = prof.username or prof.external_id
        lines.append(f"{icon} **{prof.platform.title()}** (`{name}`):")

        lines.append("  🏆 **Conquistas:**")
        if not prof.advancements.available:
            lines.append(f"    • ⚠️ *indisponível* ({prof.advancements.error_message or 'sem dados'})")
        else:
            completed = prof.advancements.total_completed if prof.advancements.total_completed is not None else 0
            lines.append(f"    • Total concluídas: {completed}")
            if prof.advancements.highlights:
                lines.append(f"    • Destaques: {', '.join(h.split('/')[-1].replace('_', ' ').title() for h in prof.advancements.highlights[:4])}")

        lines.append("  📈 **Estatísticas:**")
        if not prof.stats.available:
            lines.append(f"    • ⚠️ *indisponível* ({prof.stats.error_message or 'sem dados'})")
        else:
            lines.append(f"    • Tempo de jogo: {format_playtime(prof.stats.play_time_seconds)}")
            deaths = prof.stats.deaths if prof.stats.deaths is not None else "indisponível"
            mob_k = prof.stats.mob_kills if prof.stats.mob_kills is not None else "indisponível"
            lines.append(f"    • Mortes: {deaths} | Mobs derrotados: {mob_k}")
            if prof.stats.blocks_mined is not None:
                lines.append(f"    • Blocos minerados: {prof.stats.blocks_mined:,}")
            if prof.stats.distance_walked is not None:
                lines.append(f"    • Distância percorrida: {prof.stats.distance_walked:,}m")
        lines.append("")

    return "\n".join(lines)


class _ComparisonPaginationView(discord.ui.View):
    def __init__(
        self,
        client: MigrationEngineClient,
        audit: MigrationAuditStore,
        sources: tuple[MigrationSource, ...],
        target_discord_id: str,
        player_name: str,
        reason: str | None,
        comparison: ComparisonSummary,
        page: int = 0,
    ):
        super().__init__(timeout=180)
        self.client = client
        self.audit = audit
        self.sources = tuple(source for source in sources if source.semantic_type != "CANONICAL_TARGET")
        self.target_discord_id = str(target_discord_id)
        self.player_name = str(player_name)
        self.reason = reason
        self.comparison = comparison
        self.page = page
        self._update_button_styles()

    def _update_button_styles(self):
        self.summary_button.style = discord.ButtonStyle.primary if self.page == 0 else discord.ButtonStyle.secondary
        self.inventory_button.style = discord.ButtonStyle.primary if self.page == 1 else discord.ButtonStyle.secondary
        self.stats_button.style = discord.ButtonStyle.primary if self.page == 2 else discord.ButtonStyle.secondary

    async def _change_page(self, interaction: discord.Interaction, new_page: int):
        self.page = new_page
        self._update_button_styles()
        content = _render_preview_page(self.comparison, self.page, self.player_name, self.target_discord_id)
        await _edit_or_reply(interaction, content, self)

    @discord.ui.button(label="📄 Resumo", style=discord.ButtonStyle.primary, row=0)
    async def summary_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_page(interaction, 0)

    @discord.ui.button(label="🎒 Inventário", style=discord.ButtonStyle.secondary, row=0)
    async def inventory_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_page(interaction, 1)

    @discord.ui.button(label="🏆 Conquistas & Stats", style=discord.ButtonStyle.secondary, row=0)
    async def stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_page(interaction, 2)

    @discord.ui.button(label="☕ Usar tudo Java", style=discord.ButtonStyle.success, row=1)
    async def java_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        selected = next((source for source in self.sources if source.platform == "java"), None)
        if not selected:
            await _reply(interaction, "Source Java não está disponível.")
            return
        view = _ReplaceTargetConfirmationView(
            self.client,
            self.audit,
            selected,
            self.target_discord_id,
            self.player_name,
            self.reason,
            str(interaction.user.id),
            all_sources=self.sources,
        )
        _log_event("migration_source_selected", operator_id=str(interaction.user.id), player_name=self.player_name, platforms=("java",), target_discord_user_id=self.target_discord_id)
        await _edit_or_reply(interaction, view.render_content(), view)

    @discord.ui.button(label="📱 Usar tudo Bedrock", style=discord.ButtonStyle.success, row=1)
    async def bedrock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        selected = next((source for source in self.sources if source.platform == "bedrock"), None)
        if not selected:
            await _reply(interaction, "Source Bedrock não está disponível.")
            return
        view = _ReplaceTargetConfirmationView(
            self.client,
            self.audit,
            selected,
            self.target_discord_id,
            self.player_name,
            self.reason,
            str(interaction.user.id),
            all_sources=self.sources,
        )
        _log_event("migration_source_selected", operator_id=str(interaction.user.id), player_name=self.player_name, platforms=("bedrock",), target_discord_user_id=self.target_discord_id)
        await _edit_or_reply(interaction, view.render_content(), view)

    @discord.ui.button(label="⚙️ Escolher por categoria", style=discord.ButtonStyle.secondary, row=1)
    async def category_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cat_view = _CategoryPolicySelectionView(
            client=self.client,
            audit=self.audit,
            sources=self.sources,
            target_discord_id=self.target_discord_id,
            player_name=self.player_name,
            reason=self.reason,
            comparison=self.comparison,
        )
        content = cat_view.render_content()
        await _edit_or_reply(interaction, content, cat_view)

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        back_view = _SourceSelectionView(
            client=self.client,
            audit=self.audit,
            reason=self.reason,
            player_name=self.player_name,
            sources=self.sources,
            target_discord_id=self.target_discord_id,
        )
        content = _format_source_selection(self.player_name, self.sources, self.target_discord_id)
        await _edit_or_reply(interaction, content, back_view)


class _CategoryPolicySelectionView(discord.ui.View):
    def __init__(
        self,
        client: MigrationEngineClient,
        audit: MigrationAuditStore,
        sources: tuple[MigrationSource, ...],
        target_discord_id: str,
        player_name: str,
        reason: str | None,
        comparison: ComparisonSummary,
        current_policy: dict[str, str] | None = None,
    ):
        super().__init__(timeout=180)
        self.client = client
        self.audit = audit
        self.sources = tuple(source for source in sources if source.semantic_type != "CANONICAL_TARGET")
        self.target_discord_id = str(target_discord_id)
        self.player_name = str(player_name)
        self.reason = reason
        self.comparison = comparison
        self.policy = dict(current_policy or {"playerdata": "java", "advancements": "java", "stats": "java"})
        self._update_buttons()

    def _update_buttons(self):
        self.pd_java.style = discord.ButtonStyle.primary if self.policy.get("playerdata") == "java" else discord.ButtonStyle.secondary
        self.pd_bedrock.style = discord.ButtonStyle.primary if self.policy.get("playerdata") == "bedrock" else discord.ButtonStyle.secondary

        self.adv_java.style = discord.ButtonStyle.primary if self.policy.get("advancements") == "java" else discord.ButtonStyle.secondary
        self.adv_bedrock.style = discord.ButtonStyle.primary if self.policy.get("advancements") == "bedrock" else discord.ButtonStyle.secondary

        self.stats_java.style = discord.ButtonStyle.primary if self.policy.get("stats") == "java" else discord.ButtonStyle.secondary
        self.stats_bedrock.style = discord.ButtonStyle.primary if self.policy.get("stats") == "bedrock" else discord.ButtonStyle.secondary

    def render_content(self) -> str:
        lines = [
            "⚙️ **ESCOLHA DE FONTE POR CATEGORIA**",
            f"👤 Jogador: `{self.player_name}` | Discord: <@{self.target_discord_id}>",
            "",
            "Selecione a fonte da verdade para cada conjunto de dados:",
            f"• 🎒 **Playerdata** (inventário, ender chest, XP, vida): **{self.policy['playerdata'].title()}**",
            f"• 🏆 **Conquistas** (advancements): **{self.policy['advancements'].title()}**",
            f"• 📈 **Estatísticas** (tempo de jogo, mortes, kills): **{self.policy['stats'].title()}**",
            "",
            "Clique nos botões para alternar as fontes e em 'Confirmar' para gerar o plano.",
        ]
        return "\n".join(lines)

    async def _toggle(self, interaction: discord.Interaction, dataset: str, platform: str):
        self.policy[dataset] = platform
        self._update_buttons()
        await _edit_or_reply(interaction, self.render_content(), self)

    @discord.ui.button(label="🎒 Playerdata: Java", style=discord.ButtonStyle.primary, row=0)
    async def pd_java(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "playerdata", "java")

    @discord.ui.button(label="🎒 Playerdata: Bedrock", style=discord.ButtonStyle.secondary, row=0)
    async def pd_bedrock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "playerdata", "bedrock")

    @discord.ui.button(label="🏆 Conquistas: Java", style=discord.ButtonStyle.primary, row=1)
    async def adv_java(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "advancements", "java")

    @discord.ui.button(label="🏆 Conquistas: Bedrock", style=discord.ButtonStyle.secondary, row=1)
    async def adv_bedrock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "advancements", "bedrock")

    @discord.ui.button(label="📈 Stats: Java", style=discord.ButtonStyle.primary, row=2)
    async def stats_java(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "stats", "java")

    @discord.ui.button(label="📈 Stats: Bedrock", style=discord.ButtonStyle.secondary, row=2)
    async def stats_bedrock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "stats", "bedrock")

    @discord.ui.button(label="✅ Confirmar migração", style=discord.ButtonStyle.success, row=3)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        policy = dict(self.policy)
        request = _multi_source_request(self.sources, self.target_discord_id, policy, self.reason)
        _log_event("migration_dataset_policy_selected", operator_id=str(interaction.user.id), platforms=tuple(source.platform for source in self.sources), policy=policy)
        await _plan(interaction, self.client, self.audit, request)

    @discord.ui.button(label="↩️ Voltar ao preview", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        preview_view = _ComparisonPaginationView(
            client=self.client,
            audit=self.audit,
            sources=self.sources,
            target_discord_id=self.target_discord_id,
            player_name=self.player_name,
            reason=self.reason,
            comparison=self.comparison,
            page=0,
        )
        content = _render_preview_page(self.comparison, 0, self.player_name, self.target_discord_id)
        await _edit_or_reply(interaction, content, preview_view)


async def _show_comparison_preview(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    sources: tuple[MigrationSource, ...],
    target_discord_id: str,
    player_name: str,
    reason: str | None,
) -> None:
    operator_id = _user_id(target)
    _log_event("migration_preview_requested", operator_id=operator_id, player_name=player_name, platforms=tuple(s.platform for s in sources))
    audit.record("MIGRATION_PREVIEW", discord_user_id=operator_id, result="requested")

    preview_req = MigrationPreviewRequest(
        sources=sources,
        target={"discord_user_id": target_discord_id},
    )
    try:
        preview_res = await client.preview(preview_req)
        profiles = preview_res.sources
    except MigrationEngineNotConfigured:
        profiles = tuple(
            SourceProfilePreview(
                platform=s.platform,
                external_id=s.external_id,
                username=s.username,
                playerdata=PlayerdataPreview(available=False, error_message="Engine não configurado"),
                advancements=AdvancementsPreview(available=False, error_message="Engine não configurado"),
                stats=StatsPreview(available=False, error_message="Engine não configurado"),
            )
            for s in sources
        )
    except MigrationEngineError as error:
        _log_engine_error("migration_preview_failure", error)
        profiles = tuple(
            SourceProfilePreview(
                platform=s.platform,
                external_id=s.external_id,
                username=s.username,
                playerdata=PlayerdataPreview(available=False, error_message=f"Indisponível ({error.code.value})"),
                advancements=AdvancementsPreview(available=False, error_message=f"Indisponível ({error.code.value})"),
                stats=StatsPreview(available=False, error_message=f"Indisponível ({error.code.value})"),
            )
            for s in sources
        )

    comparison = build_comparison(profiles)
    view = _ComparisonPaginationView(
        client=client,
        audit=audit,
        sources=sources,
        target_discord_id=target_discord_id,
        player_name=player_name,
        reason=reason,
        comparison=comparison,
        page=0,
    )
    content = _render_preview_page(comparison, 0, player_name, target_discord_id)
    await _edit_or_reply(target, content, view)


class _OperatorBoundView(discord.ui.View):
    def __init__(self, operator_id: str, *, timeout: float = 120) -> None:
        super().__init__(timeout=timeout)
        self.operator_id = str(operator_id)
        self.expires_at = datetime.now(UTC) + timedelta(seconds=timeout)

    async def _allowed(self, interaction: discord.Interaction, *, admin_required: bool = False) -> bool:
        if str(interaction.user.id) != self.operator_id:
            await _reply(interaction, "Este componente pertence a outro operador.")
            return False
        if datetime.now(UTC) >= self.expires_at:
            await _reply(interaction, "Este componente expirou. Gere a acao novamente.")
            return False
        if admin_required and not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao administrativa para esta acao.")
            return False
        if not admin_required and not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para esta acao.")
            return False
        return True


class _OneClickSourceView(_OperatorBoundView):
    def __init__(self, operator_id, client, audit, identity, sources, reason):
        super().__init__(operator_id)
        self.client, self.audit, self.identity, self.sources, self.reason = client, audit, identity, tuple(sources), reason
        self.consumed = False
        for source in self.sources:
            button = discord.ui.Button(label=f"Usar {source.platform.title()} — {source.username or source.external_id}", style=discord.ButtonStyle.primary)
            button.callback = self._callback_for(source)
            self.add_item(button)

    def _callback_for(self, source):
        async def callback(interaction):
            if not await self._allowed(interaction):
                return
            if self.consumed:
                await _reply(interaction, "Esta operacao ja foi iniciada.")
                return
            self.consumed = True
            for child in self.children:
                child.disabled = True
            await _execute_one_click(interaction, self.client, self.audit, identity=self.identity, source=source, all_sources=self.sources, reason=self.reason)
        return callback


class _PlanActionView(_OperatorBoundView):
    def __init__(
        self,
        client: MigrationEngineClient,
        audit: MigrationAuditStore,
        confirmations: MigrationConfirmationStore | None,
        plan: MigrationPlan,
        operator_id: str,
    ) -> None:
        super().__init__(operator_id)
        self.client = client
        self.audit = audit
        self.confirmations = confirmations
        self.plan = plan
        self.migration_id = plan.migration_id

    @discord.ui.button(label="Executar migracao", style=discord.ButtonStyle.danger)
    async def execute_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction, admin_required=True):
            return
        if (self.plan.status or "").upper() != "READY" or self.plan.blockers or self.plan.conflicts:
            await _reply(interaction, "Este plano possui impedimentos e não pode ser executado.")
            return
        confirmations = self.confirmations or getattr(interaction.client, "_migration_confirmations", None)
        if confirmations is None:
            await _reply(interaction, "Confirmacoes de migration indisponiveis.")
            return
        await _request_confirmation(interaction, self.client, self.audit, confirmations, "execute", self.migration_id)

    @discord.ui.button(label="Cancelar/fechar", style=discord.ButtonStyle.secondary)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        for child in self.children:
            child.disabled = True
        await _reply(interaction, "Painel de migration fechado.")


class _ExecutionActionView(_OperatorBoundView):
    def __init__(
        self,
        client: MigrationEngineClient,
        audit: MigrationAuditStore,
        confirmations: MigrationConfirmationStore,
        migration_id: str,
        operator_id: str,
        *,
        rollback_available: bool,
    ) -> None:
        super().__init__(operator_id)
        self.client = client
        self.audit = audit
        self.confirmations = confirmations
        self.migration_id = migration_id
        if not rollback_available:
            self.rollback_button.disabled = True

    @discord.ui.button(label="Ver status", style=discord.ButtonStyle.primary)
    async def status_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        await _status(interaction, self.client, self.audit, self.migration_id)

    @discord.ui.button(label="Fazer rollback", style=discord.ButtonStyle.danger)
    async def rollback_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction, admin_required=True):
            return
        await _request_confirmation(interaction, self.client, self.audit, self.confirmations, "rollback", self.migration_id)


class _ConfirmationView(_OperatorBoundView):
    def __init__(
        self,
        client: MigrationEngineClient,
        audit: MigrationAuditStore,
        confirmations: MigrationConfirmationStore,
        action: str,
        migration_id: str,
        operator_id: str,
        token: str,
    ) -> None:
        super().__init__(operator_id)
        self.client = client
        self.audit = audit
        self.confirmations = confirmations
        self.action = action
        self.migration_id = migration_id
        self.token = token

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction, admin_required=True):
            return
        for child in self.children:
            child.disabled = True
        await _confirm(interaction, self.client, self.audit, self.confirmations, self.action, self.migration_id, self.token)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction, admin_required=True):
            return
        for child in self.children:
            child.disabled = True
        await _reply(interaction, "Confirmacao cancelada. Nada foi executado.")
