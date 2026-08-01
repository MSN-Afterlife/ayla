import platform
import sys
from datetime import datetime
from time import perf_counter

import discord
from discord.ext import commands
from discord.utils import format_dt
from discord.utils import utcnow

from bot.services.calculator import calculate


async def _send(ctx: commands.Context, *args, **kwargs) -> discord.Message | None:
    try:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()

        return await ctx.send(*args, **kwargs)
    except discord.NotFound:
        if ctx.channel:
            return await ctx.channel.send(*args, **kwargs)

    return None


def setup_basic_commands(bot: commands.Bot) -> None:
    @bot.hybrid_command(name="ping", description="Mostra a latencia atual do bot.")
    async def ping(ctx: commands.Context) -> None:
        started = perf_counter()
        message = await _send(ctx, embed=_build_ping_embed(ctx, bot, None, measuring=True))
        response_ms = round((perf_counter() - started) * 1000)

        if message:
            await message.edit(embed=_build_ping_embed(ctx, bot, response_ms))

    @bot.hybrid_command(name="hello", description="Recebe uma saudacao do bot.")
    async def hello(ctx: commands.Context) -> None:
        await _send(ctx, f"Ola, {ctx.author.mention}! Tudo bem?")

    @bot.hybrid_command(name="say", description="Faz o bot repetir uma mensagem.")
    async def say(ctx: commands.Context, *, message: str) -> None:
        await _send(ctx, message)

    @bot.hybrid_command(name="calc", aliases=["soma"], description="Calcula uma operacao simples.")
    async def calc(ctx: commands.Context, a: float, operator: str, b: float) -> None:
        try:
            result = calculate(a, operator, b)
        except ValueError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"O resultado de {a} {result.symbol} {b} e {result.value}")


def _build_ping_embed(
    ctx: commands.Context,
    bot: commands.Bot,
    response_ms: int | None,
    *,
    measuring: bool = False,
) -> discord.Embed:
    gateway_ms = round(bot.latency * 1000)
    uptime = _format_uptime(getattr(bot, "started_at", None))
    shard_id = ctx.guild.shard_id if ctx.guild else getattr(bot, "shard_id", None)
    shard_count = getattr(bot, "shard_count", None)
    shard_text = "Nao usando shards"
    if shard_id is not None:
        shard_text = f"{shard_id + 1}/{shard_count}" if shard_count else str(shard_id)

    embed = discord.Embed(
        title="Pong! Diagnostico do bot",
        description="Medindo..." if measuring else "Conexao ativa e comandos respondendo.",
        color=_latency_color(gateway_ms),
        timestamp=utcnow(),
    )
    embed.add_field(name="Gateway", value=f"`{gateway_ms}ms`", inline=True)
    embed.add_field(name="Resposta", value="`medindo...`" if response_ms is None else f"`{response_ms}ms`", inline=True)
    embed.add_field(name="Shard", value=shard_text, inline=True)
    embed.add_field(name="Uptime", value=uptime, inline=False)
    embed.add_field(name="Servidores", value=f"`{len(bot.guilds)}`", inline=True)
    embed.add_field(name="Usuarios em cache", value=f"`{len(bot.users)}`", inline=True)
    embed.add_field(name="Comandos", value=f"`{len(bot.commands)}` texto | `{len(bot.tree.get_commands())}` slash", inline=True)
    embed.add_field(name="Discord.py", value=f"`{discord.__version__}`", inline=True)
    embed.add_field(name="Python", value=f"`{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}`", inline=True)
    embed.add_field(name="Sistema", value=f"`{platform.system()} {platform.release()}`", inline=True)

    if bot.user:
        embed.set_footer(text=f"Logado como {bot.user} | Prefixo: {bot.command_prefix}")

    return embed


def _latency_color(latency_ms: int) -> int:
    if latency_ms < 100:
        return 0x2ECC71
    if latency_ms < 250:
        return 0xF1C40F
    return 0xE74C3C


def _format_uptime(started_at: datetime | None) -> str:
    if not started_at:
        return "`desconhecido`"

    total_seconds = max(0, int((utcnow() - started_at).total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return f"`{' '.join(parts)}` desde {format_dt(started_at, style='R')}"
