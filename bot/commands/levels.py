import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.level_images import build_leaderboard_card
from bot.services.level_images import build_profile_card
from bot.services.level_service import LevelProfile
from bot.services.level_service import LevelService


def setup_level_commands(bot: commands.Bot, settings: Settings) -> None:
    level_service = LevelService(settings)

    @bot.command(name="level", aliases=["lvl", "rank", "xp"])
    async def level(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        if not ctx.guild:
            await ctx.send("Esse comando de level local funciona dentro de um servidor.")
            return

        profile = level_service.get_guild_profile(ctx.guild.id, target.id, target.display_name)
        file = await build_profile_card(target, profile, f"Rank local - {ctx.guild.name}")
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
        file = await build_profile_card(target, profile, f"Rank local - {interaction.guild.name}")
        await interaction.followup.send(file=file)

    @bot.command(name="levelglobal", aliases=["globallevel", "globalrank", "rankglobal"])
    async def level_global(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        profile = level_service.get_global_profile(target.id, target.display_name)
        file = await build_profile_card(target, profile, "Rank global")
        await ctx.send(file=file)

    @bot.tree.command(name="levelglobal", description="Mostra seu level e rank global.")
    @app_commands.describe(member="Usuario para consultar.")
    async def level_global_slash(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        profile = level_service.get_global_profile(target.id, target.display_name)
        await interaction.response.defer()
        file = await build_profile_card(target, profile, "Rank global")
        await interaction.followup.send(file=file)

    @bot.command(name="leaderboard", aliases=["lb", "top", "ranking"])
    async def leaderboard(ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("Esse ranking local funciona dentro de um servidor.")
            return

        profiles = level_service.get_guild_leaderboard(ctx.guild.id)
        file = await build_leaderboard_card("Ranking local", ctx.guild.name, profiles, ctx.guild)
        await ctx.send(file=file)

    @bot.tree.command(name="leaderboard", description="Mostra o ranking local de levels.")
    async def leaderboard_slash(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse ranking local funciona dentro de um servidor.", ephemeral=True)
            return

        profiles = level_service.get_guild_leaderboard(interaction.guild.id)
        await interaction.response.defer()
        file = await build_leaderboard_card("Ranking local", interaction.guild.name, profiles, interaction.guild)
        await interaction.followup.send(file=file)

    @bot.command(name="leaderboardglobal", aliases=["lbglobal", "topglobal", "rankingglobal"])
    async def leaderboard_global(ctx: commands.Context) -> None:
        profiles = level_service.get_global_leaderboard()
        file = await build_leaderboard_card("Ranking global", "Todos os servidores", profiles, ctx.guild)
        await ctx.send(file=file)

    @bot.tree.command(name="leaderboardglobal", description="Mostra o ranking global de levels.")
    async def leaderboard_global_slash(interaction: discord.Interaction) -> None:
        profiles = level_service.get_global_leaderboard()
        await interaction.response.defer()
        file = await build_leaderboard_card("Ranking global", "Todos os servidores", profiles, interaction.guild)
        await interaction.followup.send(file=file)


def _profile_embed(member: discord.Member | discord.User, profile: LevelProfile, title: str, scope: str) -> discord.Embed:
    progress = max(profile.xp - profile.current_level_xp, 0)
    needed = max(profile.next_level_xp - profile.current_level_xp, 1)
    percent = min(progress / needed, 1)
    progress_bar = _progress_bar(percent)

    embed = discord.Embed(
        title=f"{title} de {member.display_name}",
        description=f"`#{profile.rank}` em {scope}",
        color=0x5865F2,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=str(profile.level), inline=True)
    embed.add_field(name="XP total", value=str(profile.xp), inline=True)
    embed.add_field(name="Progresso", value=f"{progress}/{needed} XP\n{progress_bar}", inline=False)
    return embed


def _leaderboard_embed(title: str, scope: str, profiles: list[LevelProfile]) -> discord.Embed:
    description = "\n".join(
        f"`#{profile.rank}` **{profile.user_name}** - Level {profile.level} ({profile.xp} XP)"
        for profile in profiles
    )

    embed = discord.Embed(
        title=title,
        description=description or "Ainda nao tem ninguem nesse ranking.",
        color=0xF1C40F,
    )
    embed.set_footer(text=scope)
    return embed


def _progress_bar(percent: float, size: int = 12) -> str:
    filled = round(percent * size)
    empty = size - filled
    return "[" + "#" * filled + "-" * empty + f"] {percent:.0%}"
