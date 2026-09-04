import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.minecraft_identity import MinecraftConflict, MinecraftIdentityStore, MinecraftLinkError

logger = logging.getLogger(__name__)


def setup_minecraft_commands(bot: commands.Bot, store: MinecraftIdentityStore) -> None:
    @bot.group(name="minecraft", invoke_without_command=True)
    async def minecraft_prefix(ctx: commands.Context) -> None:
        await ctx.send("Use `a!minecraft link CODIGO` ou `a!minecraft status`.")

    @minecraft_prefix.command(name="link")
    async def minecraft_link_prefix(ctx: commands.Context, code: str) -> None:
        await _link(ctx, store, code)

    @minecraft_prefix.command(name="status")
    async def minecraft_status_prefix(ctx: commands.Context) -> None:
        await _status(ctx, store)

    group = app_commands.Group(name="minecraft", description="Vinculacao da conta Minecraft")

    @group.command(name="link", description="Vincula um codigo Minecraft a sua conta Discord")
    @app_commands.describe(code="Codigo de 6 caracteres recebido ao entrar no Minecraft")
    async def minecraft_link_slash(interaction: discord.Interaction, code: str) -> None:
        await _link(interaction, store, code)

    @group.command(name="status", description="Mostra o status da sua conta Minecraft")
    async def minecraft_status_slash(interaction: discord.Interaction) -> None:
        await _status(interaction, store)

    bot.tree.add_command(group)


async def _link(target, store: MinecraftIdentityStore, code: str) -> None:
    user = target.user if isinstance(target, discord.Interaction) else target.author
    try:
        result = store.link_code(str(user.id), user.display_name, code)
    except MinecraftConflict as error:
        logger.warning("Minecraft link conflict discord_user_id=%s reason=%s", user.id, error)
        await _reply(target, f"Nao foi possivel vincular: {error}")
        return
    except MinecraftLinkError as error:
        await _reply(target, str(error))
        return
    account = result["minecraft_account"]
    identity = result["identity"]
    await _reply(target, f"✅ Conta Minecraft vinculada com sucesso.\n\nMinecraft: {account['current_username']}\nIdentidade: {identity['canonical_name']}")


async def _status(target, store: MinecraftIdentityStore) -> None:
    user = target.user if isinstance(target, discord.Interaction) else target.author
    # This query intentionally exposes only Discord-owned status, never internal UUIDs.
    with store._connect() as connection:
        identity = connection.execute("SELECT * FROM minecraft_identities WHERE discord_user_id=?", (str(user.id),)).fetchone()
        account = connection.execute("SELECT * FROM minecraft_accounts WHERE identity_id=? AND platform='java'", (identity["id"],)).fetchone() if identity else None
    if not account:
        await _reply(target, "Sua conta Java ainda nao esta vinculada.")
        return
    await _reply(target, f"Conta Java: vinculada\nMinecraft: {account['current_username']}\nIdentidade: {identity['canonical_name']}\nStatus: {'enabled' if identity['enabled'] else 'disabled'}")


async def _reply(target, message: str) -> None:
    if isinstance(target, discord.Interaction):
        if target.response.is_done():
            await target.followup.send(message, ephemeral=True)
        else:
            await target.response.send_message(message, ephemeral=True)
    else:
        await target.send(message)
