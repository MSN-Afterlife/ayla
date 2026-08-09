import functools
import os
import platform
import sys
import time
import traceback
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


DEFAULT_DEBUG_GUILD_ID = 1472950966113276068
DEFAULT_DEBUG_LOG_PATH = "data/command-debug.log"
DEFAULT_DEBUG_REPORT_DIR = "data/command-debug-reports"
DEBUG_GUILD_ID = int(os.getenv("COMMAND_DEBUG_GUILD_ID", str(DEFAULT_DEBUG_GUILD_ID)))
DEBUG_LOG_PATH = Path(os.getenv("COMMAND_DEBUG_LOG_PATH", DEFAULT_DEBUG_LOG_PATH))
DEBUG_REPORT_DIR = Path(os.getenv("COMMAND_DEBUG_REPORT_DIR", DEFAULT_DEBUG_REPORT_DIR))


def setup_command_debug(bot: commands.Bot) -> None:
    @bot.before_invoke
    async def before_text_command(ctx: commands.Context) -> None:
        if not _is_debug_guild(ctx.guild):
            return

        ctx._ayla_debug_started_at = time.perf_counter()
        _log(
            "TEXT START",
            command=ctx.command.qualified_name if ctx.command else None,
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            content=ctx.message.content,
        )

    @bot.after_invoke
    async def after_text_command(ctx: commands.Context) -> None:
        if not _is_debug_guild(ctx.guild):
            return

        _log(
            "TEXT END",
            command=ctx.command.qualified_name if ctx.command else None,
            guild=ctx.guild,
            channel=ctx.channel,
            user=ctx.author,
            elapsed_ms=_elapsed_ms(getattr(ctx, "_ayla_debug_started_at", None)),
        )

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        report_path = None
        if _is_debug_guild(ctx.guild):
            report_path = _log(
                "TEXT ERROR",
                command=ctx.command.qualified_name if ctx.command else None,
                guild=ctx.guild,
                channel=ctx.channel,
                user=ctx.author,
                elapsed_ms=_elapsed_ms(getattr(ctx, "_ayla_debug_started_at", None)),
                error=repr(error),
                traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)),
            )

        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Voce nao tem permissao para usar esse comando.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Nao consegui entender um dos argumentos desse comando.")
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Faltou informar um argumento obrigatorio.")
            return

        await _send_failure_message(ctx, "Esse comando falhou. Segue o debug em anexo.", report_path)

    _wrap_app_commands(bot)

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        report_path = None
        if _is_debug_guild(interaction.guild):
            report_path = _log(
                "SLASH ERROR",
                command=interaction.command.qualified_name if interaction.command else None,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                options=_interaction_options(interaction),
                elapsed_ms=_elapsed_ms(getattr(interaction, "_ayla_debug_started_at", None)),
                error=repr(error),
                traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)),
            )

        message = "Esse comando falhou. Segue o debug em anexo."
        file = _debug_file(report_path)
        if interaction.response.is_done():
            await interaction.followup.send(message, file=file, ephemeral=True)
        else:
            await interaction.response.send_message(message, file=file, ephemeral=True)


def _wrap_app_commands(bot: commands.Bot) -> None:
    for command in bot.tree.walk_commands():
        if isinstance(command, app_commands.Command):
            _wrap_app_command(command)


def _wrap_app_command(command: app_commands.Command[Any, ..., Any]) -> None:
    if getattr(command, "_ayla_debug_wrapped", False):
        return

    original = command.callback

    @functools.wraps(original)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        interaction = _find_interaction(args, kwargs)
        started_at = time.perf_counter()
        if interaction and _is_debug_guild(interaction.guild):
            interaction._ayla_debug_started_at = started_at
            _log(
                "SLASH START",
                command=command.qualified_name,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                options=_interaction_options(interaction),
                callback_args=_safe_repr(args),
                callback_kwargs=_safe_repr(kwargs),
            )

        try:
            result = await _call(original, *args, **kwargs)
        except Exception as error:
            if interaction and _is_debug_guild(interaction.guild):
                _log(
                    "SLASH EXCEPTION",
                    command=command.qualified_name,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    options=_interaction_options(interaction),
                    elapsed_ms=_elapsed_ms(started_at),
                    error=repr(error),
                    traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)),
                )
            raise

        if interaction and _is_debug_guild(interaction.guild):
            _log(
                "SLASH END",
                command=command.qualified_name,
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                options=_interaction_options(interaction),
                elapsed_ms=_elapsed_ms(started_at),
            )
        return result

    command._callback = wrapped
    command._ayla_debug_wrapped = True


async def _call(callback: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    return await callback(*args, **kwargs)


def _find_interaction(args: tuple[Any, ...], kwargs: dict[str, Any]) -> discord.Interaction | None:
    for value in (*args, *kwargs.values()):
        if isinstance(value, discord.Interaction):
            return value
    return None


def _is_debug_guild(guild: discord.Guild | None) -> bool:
    return guild is not None and guild.id == DEBUG_GUILD_ID


def _interaction_options(interaction: discord.Interaction) -> Any:
    if not interaction.data:
        return None
    return interaction.data.get("options")


def _elapsed_ms(started_at: float | None) -> int | None:
    if started_at is None:
        return None
    return round((time.perf_counter() - started_at) * 1000)


async def _send_failure_message(ctx: commands.Context, message: str, report_path: Path | None) -> None:
    file = _debug_file(report_path)
    await ctx.send(message, file=file)


def _debug_file(report_path: Path | None) -> discord.File | None:
    if report_path is None:
        return None
    try:
        return discord.File(report_path)
    except OSError as error:
        print(f"[AYLA COMMAND DEBUG] failed to attach {report_path}: {error}", flush=True)
        return None


def _log(event: str, **fields: Any) -> Path | None:
    timestamp = datetime.now(UTC).isoformat()
    lines = [
        "=" * 88,
        f"[AYLA COMMAND DEBUG] {timestamp} {event}",
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"pid: {os.getpid()}",
        f"cwd: {Path.cwd()}",
    ]
    lines.extend(f"{key}: {_format_value(value)}" for key, value in fields.items())
    text = "\n".join(lines)

    print(text, flush=True)
    _append_debug_file(text)
    return _write_debug_report(event, text)


def _append_debug_file(text: str) -> None:
    try:
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(text)
            file.write("\n")
    except OSError as error:
        print(f"[AYLA COMMAND DEBUG] failed to write {DEBUG_LOG_PATH}: {error}", flush=True)


def _write_debug_report(event: str, text: str) -> Path | None:
    try:
        DEBUG_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        safe_event = event.lower().replace(" ", "-")
        report_path = DEBUG_REPORT_DIR / f"{timestamp}-{safe_event}.txt"
        report_path.write_text(text + "\n", encoding="utf-8")
        return report_path
    except OSError as error:
        print(f"[AYLA COMMAND DEBUG] failed to write report in {DEBUG_REPORT_DIR}: {error}", flush=True)
        return None


def _format_value(value: Any) -> str:
    if isinstance(value, discord.Guild):
        return f"{value.name} ({value.id})"
    if isinstance(value, discord.abc.GuildChannel):
        return f"#{value.name} ({value.id})"
    if isinstance(value, discord.User | discord.Member):
        return f"{value} ({value.id})"
    return _safe_repr(value)


def _safe_repr(value: Any, limit: int = 1800) -> str:
    text = repr(value)
    if len(text) > limit:
        return f"{text[:limit]}... <truncated {len(text) - limit} chars>"
    return text
