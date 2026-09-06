import logging
import uuid

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
    MigrationStatus,
    PresenceState,
)


logger = logging.getLogger(__name__)


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
        await _inspect(ctx, client, audit, _request_from_target(target))

    @migration_prefix.command(name="plan")
    @commands.has_permissions(manage_guild=True)
    async def plan_prefix(ctx: commands.Context, target: str, *, reason: str = "") -> None:
        await _plan(ctx, client, audit, _plan_request_from_target(target, reason or None))

    @migration_prefix.command(name="status")
    @commands.has_permissions(manage_guild=True)
    async def status_prefix(ctx: commands.Context, migration_id: str) -> None:
        await _status(ctx, client, audit, migration_id)

    @migration_prefix.command(name="execute")
    @commands.has_permissions(administrator=True)
    async def execute_prefix(ctx: commands.Context, migration_id: str) -> None:
        await _request_confirmation(ctx, client, audit, confirmations, "execute", migration_id)

    @migration_prefix.command(name="rollback")
    @commands.has_permissions(administrator=True)
    async def rollback_prefix(ctx: commands.Context, migration_id: str) -> None:
        await _request_confirmation(ctx, client, audit, confirmations, "rollback", migration_id)

    @migration_prefix.command(name="confirm")
    @commands.has_permissions(administrator=True)
    async def confirm_prefix(ctx: commands.Context, action: str, migration_id: str, token: str) -> None:
        await _confirm(ctx, client, audit, confirmations, action, migration_id, token)

    group = app_commands.Group(name="migration", description="Orquestracao de migrations Minecraft")

    @group.command(name="inspect", description="Inspeciona uma identidade Minecraft sem alterar dados")
    @app_commands.describe(target="Discord ID, canonical UUID ou java:uuid/bedrock:xuid")
    async def inspect_slash(interaction: discord.Interaction, target: str) -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para inspecionar migrations.")
            return
        await _inspect(interaction, client, audit, _request_from_target(target))

    @group.command(name="plan", description="Gera um plano de migration sem executar alteracoes")
    @app_commands.describe(target="Discord ID, canonical UUID ou java:uuid/bedrock:xuid", reason="Motivo administrativo opcional")
    async def plan_slash(interaction: discord.Interaction, target: str, reason: str = "") -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para planejar migrations.")
            return
        await _plan(interaction, client, audit, _plan_request_from_target(target, reason or None))

    @group.command(name="status", description="Consulta o estado de uma migration")
    async def status_slash(interaction: discord.Interaction, migration_id: str) -> None:
        if not _is_staff(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para consultar migrations.")
            return
        await _status(interaction, client, audit, migration_id)

    @group.command(name="execute", description="Cria uma confirmacao para executar migration")
    async def execute_slash(interaction: discord.Interaction, migration_id: str) -> None:
        if not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para executar migrations.")
            return
        await _request_confirmation(interaction, client, audit, confirmations, "execute", migration_id)

    @group.command(name="rollback", description="Cria uma confirmacao para rollback de migration")
    async def rollback_slash(interaction: discord.Interaction, migration_id: str) -> None:
        if not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para fazer rollback de migrations.")
            return
        await _request_confirmation(interaction, client, audit, confirmations, "rollback", migration_id)

    @group.command(name="confirm", description="Confirma uma execute/rollback pendente")
    async def confirm_slash(interaction: discord.Interaction, action: str, migration_id: str, token: str) -> None:
        if not _is_admin(interaction.user):
            await _reply(interaction, "Voce nao tem permissao para confirmar migrations.")
            return
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
    try:
        result = await client.plan(request)
    except MigrationEngineError as error:
        audit.record("MIGRATION_PLAN", discord_user_id=user_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))
        return
    audit.record(
        "MIGRATION_PLAN",
        discord_user_id=user_id,
        canonical_uuid=result.canonical_target,
        migration_id=result.migration_id,
        presence=result.presence.value,
        lock_state=result.lock_state.value,
        result="ok",
    )
    await _reply(target, _format_plan(result))


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


async def _request_confirmation(
    target,
    client: MigrationEngineClient,
    audit: MigrationAuditStore,
    confirmations: MigrationConfirmationStore,
    action: str,
    migration_id: str,
) -> None:
    user_id = _user_id(target)
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
                f"Confirmacao criada para `{action}` da migration `{status.migration_id}`.",
                "Nada foi executado ainda.",
                f"Para confirmar, use `migration confirm {action} {status.migration_id} {pending.token}`.",
                f"Expira em {confirmations.ttl_seconds}s e so vale para voce.",
            ]
        ),
    )


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
            result = await client.execute(migration_id, operator_id=user_id, idempotency_key=str(uuid.uuid4()))
            audit.record(
                succeeded_event,
                discord_user_id=user_id,
                migration_id=result.migration_id,
                presence=result.presence.value,
                lock_state=result.lock_state.value,
                result=result.result,
            )
            await _reply(target, _format_execution(result.migration_id, result.result, result.migrated_datasets, result.verifications))
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
        audit.record(failed_event, discord_user_id=user_id, migration_id=migration_id, result="failed", error=str(error))
        await _reply(target, _error_message(error))


def _request_from_target(target: str) -> MigrationInspectRequest:
    target = str(target or "").strip()
    if target.casefold().startswith("java:"):
        return MigrationInspectRequest(platform="java", external_id=target.split(":", 1)[1].strip())
    if target.casefold().startswith("bedrock:"):
        return MigrationInspectRequest(platform="bedrock", external_id=target.split(":", 1)[1].strip())
    try:
        uuid.UUID(target)
        return MigrationInspectRequest(canonical_uuid=target.lower())
    except ValueError:
        pass
    return MigrationInspectRequest(discord_user_id=target)


def _plan_request_from_target(target: str, reason: str | None) -> MigrationPlanRequest:
    request = _request_from_target(target)
    return MigrationPlanRequest(
        discord_user_id=request.discord_user_id,
        canonical_uuid=request.canonical_uuid,
        platform=request.platform,
        external_id=request.external_id,
        reason=reason,
    )


def _is_staff(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and (permissions.manage_guild or permissions.administrator))


def _is_admin(user) -> bool:
    permissions = getattr(user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def _user_id(target) -> str:
    user = target.user if isinstance(target, discord.Interaction) else target.author
    return str(user.id)


async def _reply(target, message: str) -> None:
    message = message[:1900]
    if isinstance(target, discord.Interaction):
        if target.response.is_done():
            await target.followup.send(message, ephemeral=True)
        else:
            await target.response.send_message(message, ephemeral=True)
    else:
        await target.send(message)


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
        mapped = {
            MigrationErrorCode.PLAYER_ONLINE: "Jogador online; operacao recusada.",
            MigrationErrorCode.PRESENCE_UNKNOWN: "Presenca UNKNOWN; operacao recusada.",
            MigrationErrorCode.LOCK_ACTIVE: "Gateway Lock ativo; operacao recusada.",
            MigrationErrorCode.NOT_FOUND: "Migration nao encontrada.",
            MigrationErrorCode.ALREADY_EXECUTED: "Migration ja executada.",
            MigrationErrorCode.ROLLBACK_UNAVAILABLE: "Rollback indisponivel.",
            MigrationErrorCode.CONFLICT: "Conflito detectado pelo Migration Engine.",
        }
        return mapped.get(error.code, str(error))
    return str(error)


def _account(account) -> str:
    if not account:
        return "`ausente`"
    suffix = f" ({account.username})" if account.username else ""
    return f"`{account.platform}:{account.external_id}`{suffix}"


def _csv(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "`nenhum`"
