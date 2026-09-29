import platform
import os
import shutil
import socket
import sys
from datetime import datetime
from time import perf_counter

import discord
from discord.ext import commands
from discord.utils import format_dt
from discord.utils import utcnow

from bot.services.calculator import calculate
from bot.services.staging_rbac import staging_prefix_check


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

    @bot.command(name="statusgeral", aliases=["lynstatus", "aylastatus", "botstatus"])
    @commands.check(staging_prefix_check("administrator"))
    async def full_status(ctx: commands.Context) -> None:
        embed = _build_full_status_embed(ctx, bot)
        await _send(ctx, embed=embed)


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


def _build_full_status_embed(ctx: commands.Context, bot: commands.Bot) -> discord.Embed:
    gateway_ms = round(bot.latency * 1000)
    shard_id = ctx.guild.shard_id if ctx.guild else getattr(bot, "shard_id", None)
    shard_count = getattr(bot, "shard_count", None)
    shard_text = "Nao usando shards"
    if shard_id is not None:
        shard_text = f"{shard_id + 1}/{shard_count}" if shard_count else str(shard_id)

    process_memory = _process_memory()
    disk = shutil.disk_usage(os.getcwd())
    total_members = sum((guild.member_count or 0) for guild in bot.guilds)
    text_commands = sorted(command.qualified_name for command in bot.commands)
    slash_commands = sorted(command.name for command in bot.tree.get_commands())

    embed = discord.Embed(
        title="Status geral da Lyn",
        description="Diagnostico administrativo do bot e da maquina atual.",
        color=_latency_color(gateway_ms),
        timestamp=utcnow(),
    )
    embed.add_field(name="Gateway", value=f"`{gateway_ms}ms`", inline=True)
    embed.add_field(name="Uptime", value=_format_uptime(getattr(bot, "started_at", None)), inline=True)
    embed.add_field(name="Shard", value=shard_text, inline=True)
    embed.add_field(name="Servidores", value=f"`{len(bot.guilds)}`", inline=True)
    embed.add_field(name="Membros totais", value=f"`{total_members}`", inline=True)
    embed.add_field(name="Usuarios em cache", value=f"`{len(bot.users)}`", inline=True)
    embed.add_field(name="Canais em cache", value=f"`{len(list(bot.get_all_channels()))}`", inline=True)
    embed.add_field(name="Comandos texto", value=f"`{len(bot.commands)}`", inline=True)
    embed.add_field(name="Comandos slash", value=f"`{len(bot.tree.get_commands())}`", inline=True)
    embed.add_field(name="Python", value=f"`{sys.version.split()[0]}`", inline=True)
    embed.add_field(name="Discord.py", value=f"`{discord.__version__}`", inline=True)
    embed.add_field(name="Processo", value=f"`PID {os.getpid()}`", inline=True)
    embed.add_field(name="Sistema", value=f"`{platform.platform()}`", inline=False)
    embed.add_field(name="Maquina", value=f"`{platform.machine() or 'desconhecido'}` | Host `{socket.gethostname()}`", inline=False)
    embed.add_field(name="CPU", value=f"`{os.cpu_count() or 'desconhecido'}` core(s)", inline=True)
    embed.add_field(name="RAM do processo", value=process_memory, inline=True)
    embed.add_field(name="Disco livre", value=f"`{_format_bytes(disk.free)}` de `{_format_bytes(disk.total)}`", inline=True)
    embed.add_field(name="Diretorio", value=f"`{os.getcwd()}`", inline=False)
    embed.add_field(name="Prefixo", value=f"`{bot.command_prefix}`", inline=True)
    embed.add_field(name="Slash registrados", value=_compact_list(slash_commands), inline=False)
    embed.add_field(name="Texto registrados", value=_compact_list(text_commands), inline=False)

    if bot.user:
        embed.set_footer(text=f"Logado como {bot.user} | ID {bot.user.id}")

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


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def _process_memory() -> str:
    if platform.system() == "Windows":
        value = _windows_process_memory()
        return f"`{_format_bytes(value)}`" if value else "`indisponivel`"

    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return f"`{_format_bytes(usage)}`"
        return f"`{_format_bytes(usage * 1024)}`"
    except Exception:
        return "`indisponivel`"


def _windows_process_memory() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else None
    except Exception:
        return None


def _compact_list(items: list[str], limit: int = 18) -> str:
    if not items:
        return "`nenhum`"
    visible = items[:limit]
    text = ", ".join(f"`{item}`" for item in visible)
    remaining = len(items) - len(visible)
    if remaining > 0:
        text += f" e mais `{remaining}`"
    return text[:1024]
