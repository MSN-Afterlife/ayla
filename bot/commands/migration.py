import logging
import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.migration_audit import MigrationAuditStore
from bot.services.migration_confirmation import MigrationConfirmationError, MigrationConfirmationStore
from bot.services.migration_engine_client import (
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
    MigrationSource,
    PlayerReference,
    MigrationReference,
    MigrationState,
    MigrationStatus,
    PresenceState,
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
    user_id = _user_id(target)
    _log_event("migration_plan_request", operator_id=user_id, request=request.payload())
    try:
        result = await client.plan(request)
    except MigrationEngineError as error:
        _log_engine_error("migration_plan_failure", error)
        audit.record("MIGRATION_PLAN", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
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


def _multi_source_request(sources: tuple[MigrationSource, ...], discord_user_id: str, policy: dict[str, str], reason: str | None) -> MigrationPlanRequest:
    return MigrationPlanRequest(
        sources=sources,
        target={"discord_user_id": str(discord_user_id)},
        dataset_policy=policy,
        reason=reason,
    )


def _java_policy(sources: tuple[MigrationSource, ...]) -> dict[str, str]:
    preferred = "java" if any(source.platform == "java" for source in sources) else sources[0].platform
    return {dataset: preferred for dataset in ("playerdata", "advancements", "stats")}


def _policy_for_platform(platform: str) -> dict[str, str]:
    return {dataset: platform for dataset in ("playerdata", "advancements", "stats")}


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
    return bool(permissions and (permissions.manage_guild or permissions.administrator))


def _is_admin(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


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
    if _is_interaction_like(interaction) and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


def _format_inspection(result) -> str:
    identity: IdentitySummary = result.identity
    lines = [
        "Migration inspect",
        f"Identidade canonica: `{identity.canonical_uuid}`",
        f"Discord: `{identity.discord_user_id or 'desconhecido'}`",
        f"Java: {_account(identity.java)}",
        f"Bedrock: {_account(identity.bedrock)}",
        f"Presence: `{result.presence.value}`",
        f"Gateway Lock: `{result.lock_state.value}`",
        f"Dados encontrados: {_csv(result.detected_data)}",
        f"Warnings: {_csv(result.warnings)}",
        f"Blockers: {_csv(result.blockers)}",
    ]
    return "\n".join(lines)


def _format_plan(plan: MigrationPlan) -> str:
    lines = [
        "Migration PLAN gerado. NADA foi executado.",
        f"migration_id: `{plan.migration_id}`",
        f"Alvo canonico: `{plan.canonical_target}`",
        f"Presence: `{plan.presence.value}`",
        f"Gateway Lock: `{plan.lock_state.value}`",
        f"Rollback: `{'disponivel' if plan.rollback_available else 'indisponivel'}`",
        f"Operacoes: {_csv(tuple(f'{op.dataset}:{op.action}' for op in plan.operations))}",
        f"Datasets: {_csv(plan.affected_datasets)}",
        f"Conflitos: {_csv(plan.conflicts)}",
        f"Warnings: {_csv(plan.warnings)}",
        f"Blockers: {_csv(plan.blockers)}",
    ]
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
        icon = "☕" if source.platform == "java" else "📱" if source.platform == "bedrock" else "🔹"
        lines.append(f"{icon} {source.platform.title()} — {source.username or source.external_id}")
    lines.append("\nEscolha explicitamente as sources que serão migradas.")
    return "\n".join(lines)


def _plan_action_view(client, audit, plan: MigrationPlan, operator_id: str) -> discord.ui.View | None:
    if len(plan.sources or plan.source_identities) >= 2 and not _safe_multi_source_plan(plan):
        logger.warning("migration_execute_button_suppressed migration_id=%s reason=unsafe_multi_source_plan", plan.migration_id)
        return None
    return _PlanActionView(client, audit, confirmations=None, plan=plan, operator_id=operator_id)


def _safe_multi_source_plan(plan: MigrationPlan) -> bool:
    actions = {operation.action.upper() for operation in plan.operations}
    required = {"CREATE_CANONICAL_IDENTITY", "ATTACH_JAVA_IDENTITY", "ATTACH_BEDROCK_IDENTITY", "MOVE_FILE", "ARCHIVE_FILE"}
    policy = plan.dataset_policy or {}
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
        }
        details[0] = f"Motivo: {mapped.get(error.code, str(error))}"
        return "\n".join(details)
    return str(error)


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
    return [app_commands.Choice(name=item.label()[:100], value=f"nick:{item.player_name}") for item in matches[:10]]


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


class _SourceSelectionView(discord.ui.View):
    def __init__(self, client, audit, reason, player_name, sources, target_discord_id):
        super().__init__(timeout=120)
        self.client = client
        self.audit = audit
        self.reason = reason
        self.player_name = player_name
        self.sources = tuple(sources)
        self.target_discord_id = str(target_discord_id)

    async def _choose(self, interaction, selected):
        if not selected:
            await _reply(interaction, "Nenhuma source foi selecionada.")
            return
        for child in self.children:
            child.disabled = True
        _log_event("migration_source_selected", operator_id=str(interaction.user.id), player_name=self.player_name, platforms=tuple(source.platform for source in selected), target_discord_user_id=self.target_discord_id)
        request = _multi_source_request(tuple(selected), self.target_discord_id, _java_policy(tuple(selected)), None if not self.reason else self.reason)
        await _plan(interaction, self.client, self.audit, request)

    @discord.ui.button(label="Migrar Java + Bedrock", style=discord.ButtonStyle.primary)
    async def both_button(self, interaction, button):
        selected = tuple(source for source in self.sources if source.platform in {"java", "bedrock"})
        await self._choose(interaction, selected if len(selected) >= 2 else ())

    @discord.ui.button(label="Usar progresso Java", style=discord.ButtonStyle.secondary)
    async def java_button(self, interaction, button):
        selected = tuple(source for source in self.sources if source.platform == "java")
        await self._choose(interaction, selected)

    @discord.ui.button(label="Usar progresso Bedrock", style=discord.ButtonStyle.secondary)
    async def bedrock_button(self, interaction, button):
        selected = tuple(source for source in self.sources if source.platform == "bedrock")
        await self._choose(interaction, selected)


class _DatasetPolicyView(discord.ui.View):
    def __init__(self, client, audit, sources, target, reason):
        super().__init__(timeout=120)
        self.client = client
        self.audit = audit
        self.sources = tuple(sources)
        self.target = dict(target)
        self.reason = reason

    async def _choose(self, interaction, platform):
        for child in self.children:
            child.disabled = True
        policy = _policy_for_platform(platform)
        request = _multi_source_request(self.sources, self.target.get("discord_user_id", ""), policy, self.reason)
        _log_event("migration_dataset_policy_selected", operator_id=str(interaction.user.id), platforms=tuple(source.platform for source in self.sources), policy=policy)
        await _plan(interaction, self.client, self.audit, request)

    @discord.ui.button(label="Usar progresso Java", style=discord.ButtonStyle.primary)
    async def java_button(self, interaction, button):
        await self._choose(interaction, "java")

    @discord.ui.button(label="Usar progresso Bedrock", style=discord.ButtonStyle.secondary)
    async def bedrock_button(self, interaction, button):
        await self._choose(interaction, "bedrock")


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
        self.migration_id = plan.migration_id

    @discord.ui.button(label="Executar migracao", style=discord.ButtonStyle.danger)
    async def execute_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction, admin_required=True):
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
