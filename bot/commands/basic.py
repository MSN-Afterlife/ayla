from discord.ext import commands

from bot.services.calculator import calculate


def setup_basic_commands(bot: commands.Bot) -> None:
    @bot.command(name="ping")
    async def ping(ctx: commands.Context) -> None:
        await ctx.send("Pong!")

    @bot.command(name="hello")
    async def hello(ctx: commands.Context) -> None:
        await ctx.send(f"Ola, {ctx.author.mention}! Tudo bem?")

    @bot.command(name="say")
    async def say(ctx: commands.Context, *, message: str) -> None:
        await ctx.send(message)

    @bot.command(name="calc", aliases=["soma"])
    async def calc(ctx: commands.Context, a: float, operator: str, b: float) -> None:
        try:
            result = calculate(a, operator, b)
        except ValueError as error:
            await ctx.send(str(error))
            return

        await ctx.send(f"O resultado de {a} {result.symbol} {b} e {result.value}")
