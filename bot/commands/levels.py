import discord
from discord.ext import commands

from bot.config import Settings
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
        await ctx.send(embed=_profile_embed(target, profile, "Rank local", ctx.guild.name))

    @bot.command(name="levelglobal", aliases=["globallevel", "globalrank", "rankglobal"])
    async def level_global(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        profile = level_service.get_global_profile(target.id, target.display_name)
        await ctx.send(embed=_profile_embed(target, profile, "Rank global", "Todos os servidores"))

    @bot.command(name="leaderboard", aliases=["lb", "top", "ranking"])
    async def leaderboard(ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("Esse ranking local funciona dentro de um servidor.")
            return

        profiles = level_service.get_guild_leaderboard(ctx.guild.id)
        await ctx.send(embed=_leaderboard_embed("Ranking local", ctx.guild.name, profiles))

    @bot.command(name="leaderboardglobal", aliases=["lbglobal", "topglobal", "rankingglobal"])
    async def leaderboard_global(ctx: commands.Context) -> None:
        profiles = level_service.get_global_leaderboard()
        await ctx.send(embed=_leaderboard_embed("Ranking global", "Todos os servidores", profiles))


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
