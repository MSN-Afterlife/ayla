import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.authentik_service import AuthentikConfigurationError
from bot.services.authentik_service import AuthentikGroupNotFoundError
from bot.services.authentik_service import AuthentikInvalidResponseError
from bot.services.authentik_service import AuthentikService
from bot.services.authentik_service import AuthentikServiceError
from bot.services.authentik_service import AuthentikUnauthorizedError
from bot.services.authentik_service import AuthentikUnavailableError


def setup_authdev_commands(bot: commands.Bot, settings: Settings) -> None:
    service = AuthentikService(settings)
    group = app_commands.Group(name="authdev", description="Gerencia a allowlist de desenvolvedores no Authentik.")

    @group.command(name="add", description="Autoriza um usuario para acesso de desenvolvedor.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(usuario="Usuario que sera autorizado.")
    async def add(interaction: discord.Interaction, usuario: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            added, _ = await service.add_discord_id(usuario.id)
        except AuthentikServiceError as error:
            await interaction.followup.send(_friendly_error(error), ephemeral=True)
            return

        if added:
            await interaction.followup.send(f"Pronto. {usuario.mention} foi autorizado para acesso de desenvolvedor.", ephemeral=True)
            return

        await interaction.followup.send(f"{usuario.mention} ja estava autorizado para acesso de desenvolvedor.", ephemeral=True)

    @group.command(name="remove", description="Remove um usuario da allowlist de desenvolvedores.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(usuario="Usuario que sera removido.")
    async def remove(interaction: discord.Interaction, usuario: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            removed, _ = await service.remove_discord_id(usuario.id)
        except AuthentikServiceError as error:
            await interaction.followup.send(_friendly_error(error), ephemeral=True)
            return

        if removed:
            await interaction.followup.send(f"Pronto. {usuario.mention} foi removido do acesso de desenvolvedor.", ephemeral=True)
            return

        await interaction.followup.send(f"{usuario.mention} nao estava autorizado para acesso de desenvolvedor.", ephemeral=True)

    @group.command(name="list", description="Lista usuarios autorizados para acesso de desenvolvedor.")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_allowed(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            discord_ids = await service.get_allowed_discord_ids()
        except AuthentikServiceError as error:
            await interaction.followup.send(_friendly_error(error), ephemeral=True)
            return

        if not discord_ids:
            await interaction.followup.send("Nenhum usuario autorizado no momento.", ephemeral=True)
            return

        lines = [await _format_allowed_user(interaction, discord_id) for discord_id in discord_ids]
        for index, chunk in enumerate(_chunk_lines(lines)):
            embed = discord.Embed(title="Desenvolvedores autorizados" if index == 0 else None, color=0x7AA7FF)
            embed.description = "\n".join(chunk)
            await interaction.followup.send(embed=embed, ephemeral=True)

    bot.tree.add_command(group)


async def _format_allowed_user(interaction: discord.Interaction, discord_id: str) -> str:
    if not discord_id.isdigit():
        return f"- ID invalido no Authentik (`{discord_id}`)"

    user_id = int(discord_id)
    user = None
    if interaction.guild:
        user = interaction.guild.get_member(user_id)
    user = user or interaction.client.get_user(user_id)
    if user is None:
        try:
            user = await interaction.client.fetch_user(user_id)
        except discord.DiscordException:
            user = None

    label = user.mention if user else "Usuario fora do cache"
    return f"- {label} (`{discord_id}`)"


def _chunk_lines(lines: list[str], max_chars: int = 3900) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for line in lines:
        line_size = len(line) + 1
        if current and current_size + line_size > max_chars:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(line)
        current_size += line_size

    if current:
        chunks.append(current)
    return chunks


def _friendly_error(error: AuthentikServiceError) -> str:
    if isinstance(error, AuthentikConfigurationError):
        return f"Configuracao do Authentik incompleta: {error}"
    if isinstance(error, AuthentikUnauthorizedError):
        return "Authentik recusou a autenticacao/permissao. Confira o token e as permissoes configuradas."
    if isinstance(error, AuthentikGroupNotFoundError):
        return "Nao encontrei o grupo configurado no Authentik."
    if isinstance(error, AuthentikUnavailableError):
        return f"Authentik indisponivel: {error}"
    if isinstance(error, AuthentikInvalidResponseError):
        return f"Resposta inesperada do Authentik: {error}"
    return "Nao consegui atualizar a allowlist no Authentik agora."
