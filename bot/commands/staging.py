from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.migration_engine_client import MigrationEngineClient, MigrationEngineError


UTC = timezone.utc


def setup_minecraft_auth_commands(
    bot: commands.Bot, settings: Settings, client: MigrationEngineClient | None = None
) -> None:
    client = client or getattr(bot, "_migration_engine_client", None) or MigrationEngineClient(
        settings.migration_engine_base_url,
        settings.migration_engine_token,
        timeout_seconds=settings.migration_engine_timeout_seconds,
    )
    group = bot.tree.get_command("minecraft")
    if not isinstance(group, app_commands.Group):
        group = app_commands.Group(name="minecraft", description="Comandos do Minecraft")
        bot.tree.add_command(group)
    auth = app_commands.Group(name="auth", description="Controle da autenticacao Minecraft")

    @auth.command(name="status", description="Mostra se o bypass de autenticacao esta ativo")
    async def auth_status(interaction: discord.Interaction) -> None:
        if not await _guard(interaction):
            return
        await _defer(interaction)
        await _send_status(interaction, client)

    @auth.command(name="on", description="Ativa o bypass de autenticacao Minecraft")
    async def auth_on(interaction: discord.Interaction) -> None:
        if not await _guard(interaction):
            return
        await _reply(
            interaction,
            "Isso vai permitir entrar no Minecraft sem autenticacao da Ayla.",
            view=_MinecraftAuthBypassView(client, str(interaction.user.id), enabled=True),
        )

    @auth.command(name="off", description="Desativa o bypass de autenticacao Minecraft")
    async def auth_off(interaction: discord.Interaction) -> None:
        if not await _guard(interaction):
            return
        await _defer(interaction)
        await _set_and_confirm(interaction, client, enabled=False)

    group.add_command(auth)


async def _send_status(target, client: MigrationEngineClient) -> None:
    try:
        enabled = await client.staging_auth_bypass_status()
    except MigrationEngineError:
        await _reply(target, "Nao consegui consultar a autenticacao do Minecraft agora. Estado nao alterado.")
        return
    await _reply(target, f"Autenticacao do Minecraft: {'BYPASS ATIVO' if enabled else 'ATIVA'}")


async def _set_and_confirm(target, client: MigrationEngineClient, *, enabled: bool) -> None:
    try:
        await client.set_staging_auth_bypass(enabled)
        confirmed = await client.staging_auth_bypass_status()
    except MigrationEngineError:
        await _reply(target, "Nao consegui alterar o bypass de autenticacao do Minecraft. Estado nao assumido.")
        return
    if confirmed is not enabled:
        await _reply(target, "A API respondeu, mas o estado confirmado nao bateu. Estado nao assumido.")
        return
    await _reply(target, f"Bypass de autenticacao do Minecraft: {'ATIVADO' if enabled else 'DESATIVADO'}")


async def _guard(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    if not permissions or not permissions.administrator:
        await _reply(interaction, "Voce precisa ser administrador para controlar a autenticacao do Minecraft.")
        return False
    return True


async def _reply(target, message: str, *, view: discord.ui.View | None = None) -> None:
    kwargs = {"ephemeral": True}
    if view is not None:
        kwargs["view"] = view
    if hasattr(target, "response") and hasattr(target, "followup"):
        if target.response.is_done():
            await target.followup.send(message, **kwargs)
        else:
            await target.response.send_message(message, **kwargs)
    else:
        await target.send(message, **({"view": view} if view is not None else {}))


async def _defer(interaction) -> None:
    if hasattr(interaction, "response") and not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


class _MinecraftAuthBypassView(discord.ui.View):
    def __init__(self, client: MigrationEngineClient, operator_id: str, *, enabled: bool, timeout: float = 120) -> None:
        super().__init__(timeout=timeout)
        self.client = client
        self.operator_id = str(operator_id)
        self.enabled = enabled
        self.expires_at = datetime.now(UTC) + timedelta(seconds=timeout)

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.operator_id:
            await _reply(interaction, "Este controle pertence a outro operador.")
            return False
        if datetime.now(UTC) >= self.expires_at:
            await _reply(interaction, "Este controle expirou. Rode o comando novamente.")
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        if not permissions or not permissions.administrator:
            await _reply(interaction, "Voce precisa ser administrador para confirmar esta acao.")
            return False
        return True

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        for child in self.children:
            child.disabled = True
        await _defer(interaction)
        await _set_and_confirm(interaction, self.client, enabled=self.enabled)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._allowed(interaction):
            return
        for child in self.children:
            child.disabled = True
        await _reply(interaction, "Acao cancelada. Nada foi alterado.")
