import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.clickup_service import ClickUpError, ClickUpService
from bot.services.task_ai_service import TaskAIService


logger = logging.getLogger(__name__)
MAX_DESCRIPTION_LENGTH = 12000
SAO_PAULO_TIMEZONE = timezone(timedelta(hours=-3))


def setup_clickup_commands(bot: commands.Bot, settings: Settings) -> ClickUpService:
    service = ClickUpService(settings.clickup_api_token)
    task_ai_service = TaskAIService(settings)
    bot._task_ai_service = task_ai_service

    async def create_task_from_message(interaction: discord.Interaction, message: discord.Message) -> None:
        if not _is_allowed(interaction, settings.clickup_allowed_role_ids or []):
            await interaction.response.send_message(
                "Você não tem permissão para criar tarefas no ClickUp.", ephemeral=True
            )
            return

        if not settings.clickup_api_token or not settings.clickup_list_id:
            logger.error(
                "ClickUp is not configured: token=%s list_id=%s",
                bool(settings.clickup_api_token),
                bool(settings.clickup_list_id),
            )
            await interaction.response.send_message(
                "A integração com o ClickUp não está configurada. Avise um administrador.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        logger.info(
            "ClickUp task creation requested by %s from %s",
            interaction.user.id,
            message.id,
        )
        title = _task_title(message)
        description = _task_description(message, interaction.user)
        if message.content.strip():
            logger.info("Task AI analysis requested for Discord message %s", message.id)
            try:
                analysis = await task_ai_service.analyze_task(message.content)
                title = analysis.title
                description = _task_description(message, interaction.user, analysis.description)
                logger.info("Task AI analysis completed for %s", message.id)
            except Exception as error:
                logger.warning("Task AI analysis failed for %s: %s", message.id, _safe_error_name(error))
                logger.info("Using ClickUp task fallback for %s", message.id)

        try:
            task = await service.create_task(settings.clickup_list_id, title, description)
        except ClickUpError:
            await interaction.followup.send(
                "❌ Não consegui criar a tarefa no ClickUp. O erro foi registrado para análise.", ephemeral=True
            )
            return

        task_id = str(task.get("id", "desconhecido"))
        task_url = task.get("url")
        logger.info("ClickUp task %s created from Discord message %s", task_id, message.id)
        suffix = f"\n[ Abrir tarefa no ClickUp ]({task_url})" if task_url else ""
        await interaction.followup.send(f"✅ Tarefa criada no ClickUp: **{title}**{suffix}", ephemeral=True)

    context_menu = app_commands.ContextMenu(
        name="📋 Criar tarefa",
        callback=create_task_from_message,
        type=discord.AppCommandType.message,
    )
    bot.tree.add_command(context_menu)
    return service


def _is_allowed(interaction: discord.Interaction, allowed_role_ids: list[int]) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    return user.guild_permissions.administrator or any(role.id in allowed_role_ids for role in user.roles)


def _task_title(message: discord.Message) -> str:
    content = " ".join(message.content.split())
    if content:
        return content[:200]
    channel_name = getattr(message.channel, "name", "canal")
    return f"Mensagem de {message.author.display_name} em #{channel_name}"[:200]


def _task_description(
    message: discord.Message,
    creator: discord.User | discord.Member,
    ai_summary: str | None = None,
) -> str:
    content = message.content.strip() or "_(mensagem sem conteúdo textual)_"
    author_name = _display_name(message.author)
    creator_name = _display_name(creator)
    channel_name = getattr(message.channel, "name", "canal")
    guild_name = message.guild.name if message.guild else "Mensagem direta"
    lines = []
    if ai_summary:
        lines.extend(["## Resumo", ai_summary.strip(), ""])
    lines.extend([
        "## Mensagem original",
        content,
        "",
        f"**Autor:** @{author_name}",
        f"**Canal:** #{channel_name}",
        f"**Servidor:** {guild_name}",
        f"**Enviada em:** {_format_message_datetime(message.created_at)}",
        f"**Criada por:** @{creator_name}",
        "",
        f"[Ir para a mensagem original]({message.jump_url})",
        "",
        "<details>",
        "<summary>Identificadores técnicos</summary>",
        "",
        f"- Autor: `{message.author.id}`",
        f"- Canal: `{getattr(message.channel, 'id', 'indisponível')}`",
        f"- Servidor: `{message.guild.id if message.guild else 'mensagem direta'}`",
        f"- Mensagem: `{message.id}`",
        f"- Criada por: `{creator.id}`",
        "",
        "</details>",
    ])
    if message.attachments:
        lines.extend(["", "## Anexos", *[f"- {attachment.url}" for attachment in message.attachments]])
    return "\n".join(lines)[:MAX_DESCRIPTION_LENGTH]


def _safe_error_name(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    return type(error).__name__


def _display_name(user: discord.User | discord.Member) -> str:
    name = getattr(user, "display_name", None) or getattr(user, "name", "usuário")
    return name.replace("@", "＠").strip() or "usuário"


def _format_message_datetime(value: datetime) -> str:
    local_time = value.astimezone(SAO_PAULO_TIMEZONE)
    return local_time.strftime("%d/%m/%Y %H:%M")
