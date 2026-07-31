import discord
from discord.ext import commands

from bot.services.calculator import calculate


async def _send(ctx: commands.Context, *args, **kwargs) -> None:
    try:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()

        await ctx.send(*args, **kwargs)
    except discord.NotFound:
        if ctx.channel:
            await ctx.channel.send(*args, **kwargs)


def setup_basic_commands(bot: commands.Bot) -> None:
    @bot.hybrid_command(name="ping", description="Testa se o bot esta online.")
    async def ping(ctx: commands.Context) -> None:
        await _send(ctx, "Pong!")

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
