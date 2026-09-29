from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.migration_engine_client import MigrationEngineClient, MigrationEngineError
from bot.services.staging_rbac import StagingRbacStore, is_discord_administrator, is_staging_operator


UTC = timezone.utc


def setup_minecraft_auth_commands(
    bot: commands.Bot, settings: Settings, client: MigrationEngineClient | None = None,
    rbac_store: StagingRbacStore | None = None,
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
        if not await _guard(interaction, rbac_store):
            return
        await _defer(interaction)
        await _send_status(interaction, client)

    @auth.command(name="on", description="Ativa o bypass de autenticacao Minecraft")
    async def auth_on(interaction: discord.Interaction) -> None:
        if not await _guard(interaction, rbac_store):
            return
        await _reply(
            interaction,
            "Isso vai permitir entrar no Minecraft sem autenticacao da Ayla.",
            view=_MinecraftAuthBypassView(client, str(interaction.user.id), enabled=True, rbac_store=rbac_store, guild_id=interaction.guild.id if interaction.guild else None),
        )

    @auth.command(name="off", description="Desativa o bypass de autenticacao Minecraft")
    async def auth_off(interaction: discord.Interaction) -> None:
        if not await _guard(interaction, rbac_store):
            return
        await _defer(interaction)
        await _set_and_confirm(interaction, client, enabled=False)

    group.add_command(auth)

    staging = bot.tree.get_command("staging")
    if not isinstance(staging, app_commands.Group):
        staging = app_commands.Group(name="staging", description="Operacoes do ambiente de staging")
        bot.tree.add_command(staging)
    admin = app_commands.Group(name="admin", description="Gerencia cargos administrativos da Ayla Staging")

    @admin.command(name="add", description="Concede acesso operacional da Ayla Staging a um cargo")
    @app_commands.describe(cargo="Cargo que podera operar a Ayla Staging")
    @app_commands.default_permissions(administrator=True)
    async def admin_add(interaction: discord.Interaction, cargo: discord.Role) -> None:
        if not await _require_discord_administrator(interaction):
            return
        if not interaction.guild or rbac_store is None:
            await _reply(interaction, "Este comando so pode ser usado no servidor de staging.")
            return
        if getattr(getattr(cargo, "guild", None), "id", interaction.guild.id) != interaction.guild.id or cargo.is_default():
            await _reply(interaction, "Escolha um cargo comum deste servidor; @everyone nao pode ser autorizado.")
            return
        added = rbac_store.add_role(interaction.guild.id, cargo.id, interaction.user.id)
        await _reply(interaction, f"✅ O cargo {cargo.mention} {'agora possui' if added else 'ja possui'} acesso administrativo a Ayla Staging.")

    @admin.command(name="remove", description="Remove o acesso operacional de um cargo")
    @app_commands.describe(cargo="Cargo que deixara de operar a Ayla Staging")
    @app_commands.default_permissions(administrator=True)
    async def admin_remove(interaction: discord.Interaction, cargo: discord.Role) -> None:
        if not await _require_discord_administrator(interaction):
            return
        if not interaction.guild or rbac_store is None:
            await _reply(interaction, "Este comando so pode ser usado no servidor de staging.")
            return
        removed = rbac_store.remove_role(interaction.guild.id, cargo.id, interaction.user.id)
        await _reply(interaction, f"✅ O cargo {cargo.mention} {'nao possui mais' if removed else 'nao possuia'} acesso administrativo a Ayla Staging.")

    @admin.command(name="list", description="Lista os cargos administrativos da Ayla Staging")
    @app_commands.default_permissions(administrator=True)
    async def admin_list(interaction: discord.Interaction) -> None:
        if not await _require_discord_administrator(interaction):
            return
        if not interaction.guild or rbac_store is None:
            await _reply(interaction, "Este comando so pode ser usado no servidor de staging.")
            return
        role_ids = sorted(rbac_store.role_ids(interaction.guild.id))
        if not role_ids:
            await _reply(interaction, "Nenhum cargo administrativo adicional esta configurado.\nAdministradores do Discord continuam tendo acesso.")
            return
        mentions = [interaction.guild.get_role(role_id).mention if interaction.guild.get_role(role_id) else f"Cargo removido do Discord (`{role_id}`)" for role_id in role_ids]
        await _reply(interaction, "Administradores da Ayla Staging:\n\n" + "\n".join(f"• {mention}" for mention in mentions))

    staging.add_command(admin)


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


async def _guard(interaction: discord.Interaction, rbac_store: StagingRbacStore | None = None) -> bool:
    allowed = bool(rbac_store and interaction.guild and is_staging_operator(interaction.user, interaction.guild.id, rbac_store))
    if not allowed:
        await _reply(interaction, "Voce precisa ser administrador do Discord ou possuir um cargo autorizado para controlar a autenticacao do Minecraft.")
        return False
    return True


async def _require_discord_administrator(interaction: discord.Interaction) -> bool:
    if not is_discord_administrator(getattr(interaction, "user", None)):
        await _reply(interaction, "Somente administradores reais do Discord podem alterar o RBAC da Ayla Staging.")
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
    def __init__(self, client: MigrationEngineClient, operator_id: str, *, enabled: bool, timeout: float = 120, rbac_store: StagingRbacStore | None = None, guild_id: int | None = None) -> None:
        super().__init__(timeout=timeout)
        self.client = client
        self.operator_id = str(operator_id)
        self.enabled = enabled
        self.expires_at = datetime.now(UTC) + timedelta(seconds=timeout)
        self.rbac_store = rbac_store
        self.guild_id = guild_id

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.operator_id:
            await _reply(interaction, "Este controle pertence a outro operador.")
            return False
        if datetime.now(UTC) >= self.expires_at:
            await _reply(interaction, "Este controle expirou. Rode o comando novamente.")
            return False
        if self.rbac_store is not None:
            if not interaction.guild or not is_staging_operator(interaction.user, self.guild_id or interaction.guild.id, self.rbac_store):
                await _reply(interaction, "Sua autorizacao para esta acao foi removida.")
                return False
        else:
            await _reply(interaction, "Sua autorizacao para esta acao foi removida.")
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
