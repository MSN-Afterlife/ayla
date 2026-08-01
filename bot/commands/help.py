import discord
from discord import app_commands
from discord.ext import commands

from bot.commands.interactions import INTERACTIONS
from bot.config import Settings


HELP_CATEGORIES = {
    "inicio": {
        "label": "Inicio",
        "title": "Central de ajuda",
        "description": (
            "Use os botoes abaixo para navegar pelas categorias.\n"
            "O bot aceita slash commands `/` e o prefixo `{prefix}`."
        ),
        "fields": [
            ("Chat", "Mencione o bot em uma mensagem normal para conversar com a persona.", False),
            ("Canal de chat", "`/chatconfig canal #canal` define um canal fixo para conversa.", False),
            ("Atalho", "`{prefix}interacoes` mostra todas as interacoes sociais.", False),
        ],
        "color": 0x5865F2,
    },
    "geral": {
        "label": "Geral",
        "title": "Comandos gerais",
        "description": "Comandos simples de utilidade.",
        "fields": [
            ("`{prefix}ping`", "Mostra a latencia atual do bot.", True),
            ("`{prefix}hello`", "Recebe uma saudacao do bot.", True),
            ("`{prefix}say <texto>`", "Faz o bot repetir uma mensagem.", True),
            ("`{prefix}calc 10 + 5`", "Calcula soma, subtracao, multiplicacao ou divisao.", False),
            ("`/chatconfig status`", "Mostra onde o chat com a persona esta ativo.", False),
        ],
        "color": 0x3498DB,
    },
    "midia": {
        "label": "Midia",
        "title": "Comandos de midia",
        "description": "Busca o primeiro resultado encontrado nas fontes configuradas.",
        "fields": [
            ("`{prefix}gif feliz`", "Envia o primeiro GIF encontrado.", False),
            ("`{prefix}imagem gato`", "Envia a primeira imagem encontrada.", False),
            ("`{prefix}youtube musica relaxante`", "Envia o primeiro video encontrado no YouTube.", False),
        ],
        "color": 0xE67E22,
    },
    "interacoes": {
        "label": "Interacoes",
        "title": "Interacoes sociais",
        "description": f"Existem {len(INTERACTIONS)} comandos de interacao com embed e GIF.",
        "fields": [
            ("Exemplos", "`{prefix}slap @user`, `{prefix}hug @user`, `{prefix}kiss @user`, `{prefix}pat @user`, `{prefix}bonk @user`", False),
            ("Lista completa", "`{prefix}interacoes`", False),
            ("Sem alvo", "A maioria tambem funciona sem mencionar alguem.", False),
        ],
        "color": 0xFF69B4,
    },
    "level": {
        "label": "Level",
        "title": "Sistema de level",
        "description": "XP automatico com ranking local por servidor e ranking global.",
        "fields": [
            ("`{prefix}level` / `{prefix}rank`", "Mostra seu rank local.", False),
            ("`{prefix}level @user`", "Mostra o rank local de outra pessoa.", False),
            ("`{prefix}rankglobal`", "Mostra o rank global.", False),
            ("`{prefix}top` / `{prefix}ranking`", "Mostra o ranking local.", False),
            ("`{prefix}topglobal`", "Mostra o ranking global.", False),
        ],
        "color": 0xF1C40F,
    },
}


def setup_help_command(bot: commands.Bot, settings: Settings) -> None:
    @bot.command(name="help", aliases=["ajuda", "comandos", "menu"])
    async def help_command(ctx: commands.Context) -> None:
        view = HelpView(settings.command_prefix)
        await ctx.send(embed=build_help_embed("inicio", settings.command_prefix), view=view)

    @bot.tree.command(name="help", description="Abre o menu de ajuda do bot.")
    async def help_slash(interaction: discord.Interaction) -> None:
        view = HelpView("/")
        try:
            await interaction.response.defer()
            await interaction.followup.send(embed=build_help_embed("inicio", "/"), view=view)
        except discord.NotFound:
            if interaction.channel:
                await interaction.channel.send(embed=build_help_embed("inicio", "/"), view=view)


class HelpView(discord.ui.View):
    def __init__(self, prefix: str) -> None:
        super().__init__(timeout=180)
        self._prefix = prefix

        for category_id, category in HELP_CATEGORIES.items():
            self.add_item(HelpButton(category_id, category["label"], prefix))


class HelpButton(discord.ui.Button):
    def __init__(self, category_id: str, label: str, prefix: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary if category_id == "inicio" else discord.ButtonStyle.secondary)
        self._category_id = category_id
        self._prefix = prefix

    async def callback(self, interaction: discord.Interaction) -> None:
        view = HelpView(self._prefix)
        try:
            await interaction.response.defer()
            await interaction.edit_original_response(embed=build_help_embed(self._category_id, self._prefix), view=view)
        except discord.NotFound:
            if interaction.channel:
                await interaction.channel.send(embed=build_help_embed(self._category_id, self._prefix), view=view)


def build_help_embed(category_id: str, prefix: str) -> discord.Embed:
    category = HELP_CATEGORIES[category_id]
    description = category["description"].replace("{prefix}", prefix)
    embed = discord.Embed(
        title=category["title"],
        description=description,
        color=category["color"],
    )

    for name, value, inline in category["fields"]:
        embed.add_field(name=name.replace("{prefix}", prefix), value=value.replace("{prefix}", prefix), inline=inline)

    embed.set_footer(text="Use os botoes para trocar de categoria.")
    return embed
