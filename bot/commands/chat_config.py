import discord
from discord import app_commands
from discord.ext import commands

from bot.services.chat_config import ChatConfigStore
from bot.services.staging_rbac import staging_app_command_check


def setup_chat_config_commands(bot: commands.Bot, chat_config: ChatConfigStore) -> None:
    group = app_commands.Group(name="chatconfig", description="Configura o chat com IA/persona.")

    @group.command(name="canal", description="Define o canal onde a persona vai conversar.")
    @app_commands.check(staging_app_command_check("manage_guild"))
    async def set_channel(interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse comando precisa ser usado dentro de um servidor.", ephemeral=True)
            return

        chat_config.set_channel_id(interaction.guild.id, canal.id)
        await interaction.response.send_message(f"Pronto. Vou conversar no canal {canal.mention}.", ephemeral=True)

    @group.command(name="status", description="Mostra o canal configurado para conversa.")
    async def status(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse comando precisa ser usado dentro de um servidor.", ephemeral=True)
            return

        channel_id = chat_config.get_channel_id(interaction.guild.id)
        if channel_id:
            await interaction.response.send_message(f"Canal de conversa configurado: <#{channel_id}>.", ephemeral=True)
            return

        await interaction.response.send_message("Nenhum canal fixo configurado. Eu respondo quando me mencionam.", ephemeral=True)

    @group.command(name="limpar", description="Remove o canal fixo da conversa.")
    @app_commands.check(staging_app_command_check("manage_guild"))
    async def clear(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse comando precisa ser usado dentro de um servidor.", ephemeral=True)
            return

        chat_config.clear_channel_id(interaction.guild.id)
        await interaction.response.send_message("Canal fixo removido. Volto a responder por mencao em qualquer canal.", ephemeral=True)

    bot.tree.add_command(group)
