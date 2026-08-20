import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.clickup_service import ClickUpError, ClickUpService


logger = logging.getLogger(__name__)
MAX_DESCRIPTION_LENGTH = 12000


def setup_clickup_commands(bot: commands.Bot, settings: Settings) -> ClickUpService:
    service = ClickUpService(settings.clickup_api_token)

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


def _task_description(message: discord.Message, creator: discord.User | discord.Member) -> str:
    content = message.content.strip() or "_(mensagem sem conteúdo textual)_"
    lines = [
        "## Mensagem original",
        content,
        "",
        f"**Autor:** {message.author.mention}",
        f"**Canal:** {getattr(message.channel, 'mention', '#canal')}",
        f"**Servidor:** {message.guild.name if message.guild else 'Mensagem direta'}",
        f"**Enviada em:** {message.created_at.isoformat()}",
        f"**Criada por:** {creator.mention}",
        "",
        f"[Ir para a mensagem original]({message.jump_url})",
    ]
    if message.attachments:
        lines.extend(["", "## Anexos", *[f"- {attachment.url}" for attachment in message.attachments]])
    return "\n".join(lines)[:MAX_DESCRIPTION_LENGTH]
