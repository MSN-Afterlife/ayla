from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.level_images import build_leaderboard_card
from bot.services.level_images import build_level_card
from bot.services.level_images import build_profile_card
from bot.services.level_images import can_render_profile_background
from bot.services.economy_service import EconomyService
from bot.services.level_service import LevelProfile
from bot.services.level_service import LevelService
from bot.services.media_search import MediaSearch
from bot.services.media_search import MediaSearchError
from bot.services.profile_background_storage import ProfileBackgroundError
from bot.services.staging_rbac import can_use_admin_command, staging_prefix_check


BACKGROUND_MODE_CHOICES = [
    app_commands.Choice(name="Cover - preenche e corta as bordas", value="cover"),
    app_commands.Choice(name="Contain - mostra a imagem inteira", value="contain"),
    app_commands.Choice(name="Stretch - estica para caber", value="stretch"),
]


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

    @bot.command(name="addxp")
    @commands.check(staging_prefix_check("administrator"))
    async def add_xp(ctx: commands.Context, member: discord.Member, amount: int) -> None:
        if not ctx.guild:
            await ctx.send("Esse comando so funciona dentro de um servidor.")
            return

        try:
            _, guild_profile = level_service.add_xp(ctx.guild.id, member.id, member.display_name, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            f"Adicionado `{amount}` XP para {member.mention}. "
            f"XP local: `{guild_profile.xp}` | Level: `{guild_profile.level}` | Rank: `#{guild_profile.rank}`"
        )

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
        try:
            await level_service.save_profile_background_from_url(ctx.author.id, url)
        except ProfileBackgroundError as error:
            await ctx.send(str(error))
            return
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
            urls = await _valid_background_urls(media_search, query)
        except MediaSearchError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            "Escolha uma imagem para o background do perfil:",
            embed=_background_choice_embed(urls, 0),
            view=ProfileBackgroundSearchView(level_service, economy_service, media_search, query, ctx.author, ctx.guild, urls),
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

        if not await can_render_profile_background(background_url):
            await interaction.response.send_message("Nao consegui carregar essa imagem. Tente outra URL ou envie como anexo.", ephemeral=True)
            return

        profile_data = level_service.get_guild_profile(interaction.guild.id, interaction.user.id, interaction.user.display_name) if interaction.guild else level_service.get_global_profile(interaction.user.id, interaction.user.display_name)
        customization = level_service.get_profile_customization(interaction.user.id)
        preview_customization = customization.__class__(background_url=background_url, background_mode=customization.background_mode, about=customization.about)
        economy = economy_service.get_profile(interaction.user.id)
        file = await build_profile_card(interaction.user, profile_data, "Previa do perfil", preview_customization, interaction.guild, economy)
        await interaction.response.send_message(
            "Veja como o perfil vai ficar antes de confirmar:",
            file=file,
            view=ProfileBackgroundConfirmView(
                level_service,
                economy_service,
                interaction.user,
                interaction.guild,
                background_url,
                customization.background_mode,
            ),
            ephemeral=True,
        )

    @profile_group.command(name="modo", description="Define como o background se encaixa no perfil.")
    @app_commands.describe(modo="cover corta preenchendo, contain mostra tudo, stretch estica.")
    @app_commands.choices(modo=BACKGROUND_MODE_CHOICES)
    async def profile_background_mode_slash(interaction: discord.Interaction, modo: app_commands.Choice[str]) -> None:
        level_service.set_profile_background_mode(interaction.user.id, modo.value)
        await interaction.response.send_message(f"Modo do background atualizado para `{modo.value}`.", ephemeral=True)

    @profile_group.command(name="sobre", description="Define o campo sobre mim do seu perfil.")
    @app_commands.describe(texto="Texto curto que aparece no seu perfil.")
    async def profile_about_slash(interaction: discord.Interaction, texto: str) -> None:
        level_service.set_profile_about(interaction.user.id, texto[:120])
        await interaction.response.send_message("Sobre mim atualizado.", ephemeral=True)

    @bot.command(name="perfilstatus", aliases=["profilestatus"])
    async def profile_status_admin(ctx: commands.Context) -> None:
        if not ctx.guild or not isinstance(ctx.author, discord.Member) or not can_use_admin_command(ctx, required="administrator"):
            await ctx.send("Esse comando é exclusivo para administradores.")
            return

        customization = level_service.get_profile_customization(ctx.author.id)
        embed = discord.Embed(title="Perfil", color=0x5865F2)
        background_value = "Armazenado localmente" if customization.background_url and Path(customization.background_url).is_file() else customization.background_url or "Nenhum"
        embed.add_field(name="Background", value=background_value, inline=False)
        embed.add_field(name="Modo", value=customization.background_mode, inline=True)
        embed.add_field(name="Sobre mim", value=customization.about or "Nenhum", inline=False)
        await ctx.send(embed=embed)

    @profile_group.command(name="buscarbackground", description="Busca uma imagem e usa como background do seu perfil.")
    @app_commands.describe(busca="Termo para buscar a imagem.")
    async def search_profile_background_slash(interaction: discord.Interaction, busca: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            urls = await _valid_background_urls(media_search, busca)
        except MediaSearchError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return

        await interaction.followup.send(
            "Escolha uma imagem para o background do perfil:",
            embed=_background_choice_embed(urls, 0),
            view=ProfileBackgroundSearchView(level_service, economy_service, media_search, busca, interaction.user, interaction.guild, urls),
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
    def __init__(
        self,
        level_service: LevelService,
        economy_service: EconomyService,
        media_search: MediaSearch,
        query: str,
        user: discord.Member | discord.User,
        guild: discord.Guild | None,
        urls: list[str],
    ) -> None:
        super().__init__(timeout=180)
        self._level_service = level_service
        self._economy_service = economy_service
        self._media_search = media_search
        self._query = query
        self._user = user
        self._user_id = user.id
        self._guild = guild
        self._urls = urls
        self._index = 0
        self._search_limit = 40
        self._loading_more = False

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Essa escolha pertence a outra pessoa.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="-5", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await self._move(interaction, -5)

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await self._move(interaction, -1)

    @discord.ui.button(label="Previa", style=discord.ButtonStyle.primary)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        url = self._urls[self._index]
        await interaction.response.defer()
        file = await self._preview_file(url)
        await interaction.edit_original_response(
            content="Confira a previa do perfil. Confirme apenas se estiver do jeito que voce quer.",
            embed=None,
            attachments=[file],
            view=ProfileBackgroundConfirmView(
                self._level_service,
                self._economy_service,
                self._user,
                self._guild,
                url,
                self._level_service.get_profile_customization(self._user_id).background_mode,
                self,
            ),
        )

    @discord.ui.button(label="Proxima", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await self._move(interaction, 1)

    @discord.ui.button(label="+5", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await self._move(interaction, 5)

    async def _move(self, interaction: discord.Interaction, amount: int) -> None:
        target_index = self._index + amount
        if target_index >= len(self._urls):
            await interaction.response.defer()
            loaded = await self._load_more()
            if not loaded:
                self._index = len(self._urls) - 1
                await interaction.followup.send("Nao encontrei mais imagens validas por enquanto.", ephemeral=True)
                await interaction.edit_original_response(embed=_background_choice_embed(self._urls, self._index), view=self)
                return
            self._index = min(target_index, len(self._urls) - 1)
            await interaction.edit_original_response(embed=_background_choice_embed(self._urls, self._index), view=self)
            return

        if target_index < 0:
            self._index = max(0, target_index)
        else:
            self._index = target_index
        await interaction.response.edit_message(embed=_background_choice_embed(self._urls, self._index), view=self)

    async def _load_more(self) -> bool:
        if self._loading_more:
            return False

        self._loading_more = True
        try:
            previous_count = len(self._urls)
            self._search_limit += 40
            urls = await _valid_background_urls(self._media_search, self._query, limit=self._search_limit, max_valid=previous_count + 25)
            existing = set(self._urls)
            self._urls.extend(url for url in urls if url not in existing)
            return len(self._urls) > previous_count
        finally:
            self._loading_more = False

    async def _preview_file(self, url: str) -> discord.File:
        profile_data = (
            self._level_service.get_guild_profile(self._guild.id, self._user.id, self._user.display_name)
            if self._guild
            else self._level_service.get_global_profile(self._user.id, self._user.display_name)
        )
        customization = self._level_service.get_profile_customization(self._user.id)
        preview_customization = customization.__class__(background_url=url, background_mode=customization.background_mode, about=customization.about)
        economy = self._economy_service.get_profile(self._user.id)
        return await build_profile_card(self._user, profile_data, "Previa do perfil", preview_customization, self._guild, economy)


class ProfileBackgroundConfirmView(discord.ui.View):
    def __init__(
        self,
        level_service: LevelService,
        economy_service: EconomyService,
        user: discord.Member | discord.User,
        guild: discord.Guild | None,
        url: str,
        mode: str,
        search_view: ProfileBackgroundSearchView | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._level_service = level_service
        self._economy_service = economy_service
        self._user = user
        self._user_id = user.id
        self._guild = guild
        self._url = url
        self._mode = mode
        self._search_view = search_view

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("Essa confirmacao pertence a outra pessoa.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        try:
            await self._level_service.save_profile_background_from_url(self._user_id, self._url)
            self._level_service.set_profile_background_mode(self._user_id, self._mode)
        except ProfileBackgroundError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.edit_message(content=f"Background do seu perfil atualizado em modo `{self._mode}`.", attachments=[], view=None)

    @discord.ui.button(label="Cover", style=discord.ButtonStyle.secondary)
    async def cover(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change_mode(interaction, "cover")

    @discord.ui.button(label="Contain", style=discord.ButtonStyle.secondary)
    async def contain(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change_mode(interaction, "contain")

    @discord.ui.button(label="Stretch", style=discord.ButtonStyle.secondary)
    async def stretch(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._change_mode(interaction, "stretch")

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        if not self._search_view:
            await interaction.response.edit_message(content="Atualizacao cancelada.", attachments=[], view=None)
            return
        await interaction.response.edit_message(
            content="Escolha uma imagem para o background do perfil:",
            embed=_background_choice_embed(self._search_view._urls, self._search_view._index),
            attachments=[],
            view=self._search_view,
        )

    async def _change_mode(self, interaction: discord.Interaction, mode: str) -> None:
        if not await self._guard(interaction):
            return
        self._mode = mode
        await interaction.response.defer()
        file = await self._preview_file()
        await interaction.edit_original_response(
            content=f"Previa em modo `{self._mode}`. Confirme se estiver do jeito que voce quer.",
            attachments=[file],
            view=self,
        )

    async def _preview_file(self) -> discord.File:
        profile_data = (
            self._level_service.get_guild_profile(self._guild.id, self._user.id, self._user.display_name)
            if self._guild
            else self._level_service.get_global_profile(self._user.id, self._user.display_name)
        )
        customization = self._level_service.get_profile_customization(self._user.id)
        preview_customization = customization.__class__(background_url=self._url, background_mode=self._mode, about=customization.about)
        economy = self._economy_service.get_profile(self._user.id)
        return await build_profile_card(self._user, profile_data, "Previa do perfil", preview_customization, self._guild, economy)


def _background_choice_embed(urls: list[str], index: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"Imagem {index + 1}",
        description="Use os botoes para navegar e escolher.",
        color=0x5865F2,
    )
    embed.set_image(url=urls[index])
    return embed


async def _valid_background_urls(media_search: MediaSearch, query: str, *, limit: int = 40, max_valid: int = 25) -> list[str]:
    urls = await media_search.search_images(query, limit=limit)
    valid_urls = []
    for url in urls:
        if await can_render_profile_background(url):
            valid_urls.append(url)
        if len(valid_urls) >= max_valid:
            break

    if not valid_urls:
        raise MediaSearchError("Encontrei imagens, mas nenhuma carregou como background. Tente outra busca ou envie uma imagem por anexo.")

    return valid_urls


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
