import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.level_images import build_leaderboard_card
from bot.services.level_images import build_level_card
from bot.services.level_images import build_profile_card
from bot.services.economy_service import EconomyService
from bot.services.level_service import LevelProfile
from bot.services.level_service import LevelService
from bot.services.media_search import MediaSearch
from bot.services.media_search import MediaSearchError


def setup_level_commands(bot: commands.Bot, settings: Settings) -> None:
    level_service = LevelService(settings)
    economy_service = EconomyService(settings)
    media_search = MediaSearch(settings)
    profile_group = app_commands.Group(name="perfil", description="Configura e mostra seu perfil de rank.")

    @bot.command(name="level", aliases=["lvl", "rank", "xp"])
    async def level(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        if not ctx.guild:
            await ctx.send("Esse comando de level local funciona dentro de um servidor.")
            return

        profile = level_service.get_guild_profile(ctx.guild.id, target.id, target.display_name)
        customization = level_service.get_profile_customization(target.id)
        file = await build_level_card(target, profile, f"Rank local - {ctx.guild.name}", ctx.guild)
        await ctx.send(file=file)

    @bot.tree.command(name="level", description="Mostra seu level e rank local.")
    @app_commands.describe(member="Usuario para consultar.")
    async def level_slash(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        if not interaction.guild:
            await interaction.response.send_message("Esse comando de level local funciona dentro de um servidor.", ephemeral=True)
            return

        profile = level_service.get_guild_profile(interaction.guild.id, target.id, target.display_name)
        await interaction.response.defer()
        file = await build_level_card(target, profile, f"Rank local - {interaction.guild.name}", interaction.guild)
        await interaction.followup.send(file=file)

    @bot.command(name="levelglobal", aliases=["globallevel", "globalrank", "rankglobal"])
    async def level_global(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        profile = level_service.get_global_profile(target.id, target.display_name)
        file = await build_level_card(target, profile, "Rank global", ctx.guild)
        await ctx.send(file=file)

    @bot.tree.command(name="levelglobal", description="Mostra seu level e rank global.")
    @app_commands.describe(member="Usuario para consultar.")
    async def level_global_slash(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        profile = level_service.get_global_profile(target.id, target.display_name)
        await interaction.response.defer()
        file = await build_level_card(target, profile, "Rank global", interaction.guild)
        await interaction.followup.send(file=file)

    @bot.command(name="perfil", aliases=["profile"])
    async def profile(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        if not ctx.guild:
            await ctx.send("Esse perfil funciona dentro de um servidor.")
            return

        profile_data = level_service.get_guild_profile(ctx.guild.id, target.id, target.display_name)
        customization = level_service.get_profile_customization(target.id)
        economy = economy_service.get_profile(target.id)
        file = await build_profile_card(target, profile_data, f"Perfil - {ctx.guild.name}", customization, ctx.guild, economy)
        await ctx.send(file=file)

    @bot.command(name="perfilbg", aliases=["profilebg", "backgroundperfil"])
    async def profile_background(ctx: commands.Context, *, url: str) -> None:
        level_service.set_profile_background(ctx.author.id, url)
        await ctx.send("Background do seu perfil atualizado.")

    @bot.command(name="perfilbgmodo", aliases=["profilebgmode"])
    async def profile_background_mode(ctx: commands.Context, mode: str) -> None:
        mode = mode.lower()
        if mode not in {"cover", "contain", "stretch"}:
            await ctx.send("Modo invalido. Use: cover, contain ou stretch.")
            return

        level_service.set_profile_background_mode(ctx.author.id, mode)
        await ctx.send(f"Modo do background atualizado para `{mode}`.")

    @bot.command(name="perfilsobre", aliases=["profileabout"])
    async def profile_about(ctx: commands.Context, *, about: str) -> None:
        level_service.set_profile_about(ctx.author.id, about[:120])
        await ctx.send("Sobre mim atualizado.")

    @bot.command(name="perfilbgbuscar", aliases=["buscarperfilbg", "profilebgsearch"])
    async def search_profile_background(ctx: commands.Context, *, query: str) -> None:
        try:
            urls = await media_search.search_images(query, limit=5)
        except MediaSearchError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            "Escolha uma imagem para o background do perfil:",
            embed=_background_choice_embed(urls, 0),
            view=ProfileBackgroundSearchView(level_service, ctx.author.id, urls),
        )

    @bot.command(name="perfilbglimpar", aliases=["limparperfilbg", "profilebgclear"])
    async def clear_profile_background(ctx: commands.Context) -> None:
        level_service.clear_profile_background(ctx.author.id)
        await ctx.send("Background do seu perfil removido.")

    @profile_group.command(name="ver", description="Mostra seu perfil de rank.")
    @app_commands.describe(member="Usuario para consultar.")
    async def profile_slash(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        if not interaction.guild:
            await interaction.response.send_message("Esse perfil funciona dentro de um servidor.", ephemeral=True)
            return

        await interaction.response.defer()
        profile_data = level_service.get_guild_profile(interaction.guild.id, target.id, target.display_name)
        customization = level_service.get_profile_customization(target.id)
        economy = economy_service.get_profile(target.id)
        file = await build_profile_card(target, profile_data, f"Perfil - {interaction.guild.name}", customization, interaction.guild, economy)
        await interaction.followup.send(file=file)

    @profile_group.command(name="background", description="Define o background do seu perfil por URL ou anexo.")
    @app_commands.describe(url="URL direta de uma imagem.", anexo="Imagem anexada para usar como background.")
    async def profile_background_slash(
        interaction: discord.Interaction,
        url: str | None = None,
        anexo: discord.Attachment | None = None,
    ) -> None:
        background_url = anexo.url if anexo else url
        if not background_url:
            await interaction.response.send_message("Envie uma URL ou anexe uma imagem.", ephemeral=True)
            return

        level_service.set_profile_background(interaction.user.id, background_url)
        await interaction.response.send_message("Background do seu perfil atualizado.", ephemeral=True)

    @profile_group.command(name="modo", description="Define como o background se encaixa no perfil.")
    @app_commands.describe(modo="cover corta preenchendo, contain mostra tudo, stretch estica.")
    @app_commands.choices(
        modo=[
            app_commands.Choice(name="cover", value="cover"),
            app_commands.Choice(name="contain", value="contain"),
            app_commands.Choice(name="stretch", value="stretch"),
        ]
    )
    async def profile_background_mode_slash(interaction: discord.Interaction, modo: app_commands.Choice[str]) -> None:
        level_service.set_profile_background_mode(interaction.user.id, modo.value)
        await interaction.response.send_message(f"Modo do background atualizado para `{modo.value}`.", ephemeral=True)

    @profile_group.command(name="sobre", description="Define o campo sobre mim do seu perfil.")
    @app_commands.describe(texto="Texto curto que aparece no seu perfil.")
    async def profile_about_slash(interaction: discord.Interaction, texto: str) -> None:
        level_service.set_profile_about(interaction.user.id, texto[:120])
        await interaction.response.send_message("Sobre mim atualizado.", ephemeral=True)

    @profile_group.command(name="status", description="Mostra suas configuracoes de perfil.")
    async def profile_status_slash(interaction: discord.Interaction) -> None:
        customization = level_service.get_profile_customization(interaction.user.id)
        embed = discord.Embed(title="Perfil", color=0x5865F2)
        embed.add_field(name="Background", value=customization.background_url or "Nenhum", inline=False)
        embed.add_field(name="Modo", value=customization.background_mode, inline=True)
        embed.add_field(name="Sobre mim", value=customization.about or "Nenhum", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @profile_group.command(name="buscarbackground", description="Busca uma imagem e usa como background do seu perfil.")
    @app_commands.describe(busca="Termo para buscar a imagem.")
    async def search_profile_background_slash(interaction: discord.Interaction, busca: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            urls = await media_search.search_images(busca, limit=5)
        except MediaSearchError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        await interaction.followup.send(
            "Escolha uma imagem para o background do perfil:",
            embed=_background_choice_embed(urls, 0),
            view=ProfileBackgroundSearchView(level_service, interaction.user.id, urls),
            ephemeral=True,
        )

    @profile_group.command(name="limparbackground", description="Remove o background personalizado do seu perfil.")
    async def clear_profile_background_slash(interaction: discord.Interaction) -> None:
        level_service.clear_profile_background(interaction.user.id)
        await interaction.response.send_message("Background do seu perfil removido.", ephemeral=True)

    @bot.command(name="leaderboard", aliases=["lb", "top", "ranking"])
    async def leaderboard(ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("Esse ranking local funciona dentro de um servidor.")
            return

        profiles = level_service.get_guild_leaderboard(ctx.guild.id)
        users = await _resolve_leaderboard_users(bot, profiles, ctx.guild)
        file = await build_leaderboard_card("Ranking local", ctx.guild.name, profiles, ctx.guild, users)
        await ctx.send(file=file)

    @bot.tree.command(name="leaderboard", description="Mostra o ranking local de levels.")
    async def leaderboard_slash(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse ranking local funciona dentro de um servidor.", ephemeral=True)
            return

        profiles = level_service.get_guild_leaderboard(interaction.guild.id)
        await interaction.response.defer()
        users = await _resolve_leaderboard_users(bot, profiles, interaction.guild)
        file = await build_leaderboard_card("Ranking local", interaction.guild.name, profiles, interaction.guild, users)
        await interaction.followup.send(file=file)

    @bot.command(name="leaderboardglobal", aliases=["lbglobal", "topglobal", "rankingglobal"])
    async def leaderboard_global(ctx: commands.Context) -> None:
        profiles = level_service.get_global_leaderboard()
        users = await _resolve_leaderboard_users(bot, profiles, ctx.guild)
        file = await build_leaderboard_card("Ranking global", "Todos os servidores", profiles, ctx.guild, users)
        await ctx.send(file=file)

    @bot.tree.command(name="leaderboardglobal", description="Mostra o ranking global de levels.")
    async def leaderboard_global_slash(interaction: discord.Interaction) -> None:
        profiles = level_service.get_global_leaderboard()
        await interaction.response.defer()
        users = await _resolve_leaderboard_users(bot, profiles, interaction.guild)
        file = await build_leaderboard_card("Ranking global", "Todos os servidores", profiles, interaction.guild, users)
        await interaction.followup.send(file=file)

    bot.tree.add_command(profile_group)


class ProfileBackgroundSearchView(discord.ui.View):
    def __init__(self, level_service: LevelService, user_id: int, urls: list[str]) -> None:
        super().__init__(timeout=180)
        self._level_service = level_service
        self._user_id = user_id
        self._urls = urls
        self._index = 0

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Essa escolha pertence a outra pessoa.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        self._index = (self._index - 1) % len(self._urls)
        await interaction.response.edit_message(embed=_background_choice_embed(self._urls, self._index), view=self)

    @discord.ui.button(label="Escolher", style=discord.ButtonStyle.success)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        url = self._urls[self._index]
        self._level_service.set_profile_background(self._user_id, url)
        embed = discord.Embed(title="Background atualizado", description=url, color=0x5865F2)
        embed.set_image(url=url)
        await interaction.response.edit_message(content="Background do seu perfil atualizado.", embed=embed, view=None)

    @discord.ui.button(label="Proxima", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        self._index = (self._index + 1) % len(self._urls)
        await interaction.response.edit_message(embed=_background_choice_embed(self._urls, self._index), view=self)


def _background_choice_embed(urls: list[str], index: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"Imagem {index + 1}/{len(urls)}",
        description="Use os botoes para navegar e escolher.",
        color=0x5865F2,
    )
    embed.set_image(url=urls[index])
    return embed


async def _resolve_leaderboard_users(
    bot: commands.Bot,
    profiles: list[LevelProfile],
    guild: discord.Guild | None,
) -> dict[int, discord.Member | discord.User]:
    users: dict[int, discord.Member | discord.User] = {}

    for profile in profiles:
        member = guild.get_member(profile.user_id) if guild else None
        if member:
            users[profile.user_id] = member
            continue

        user = bot.get_user(profile.user_id)
        if user:
            users[profile.user_id] = user
            continue

        try:
            users[profile.user_id] = await bot.fetch_user(profile.user_id)
        except discord.HTTPException:
            continue

    return users
