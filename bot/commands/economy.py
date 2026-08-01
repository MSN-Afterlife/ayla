import discord
from discord.ext import commands

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
from bot.services.economy_service import EconomyService


def setup_economy_commands(bot: commands.Bot, settings: Settings) -> None:
    economy = EconomyService(settings)

    @bot.hybrid_command(name="saldo", aliases=["balance", "bal", "coins"], description="Mostra seu saldo.")
    async def balance(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        profile = economy.get_profile(target.id)
        await ctx.send(f"Saldo de **{target.display_name}**: `{profile.balance}` moedas | Daily streak: `{profile.daily_streak}`")

    @bot.hybrid_command(name="daily", aliases=["diario"], description="Coleta suas moedas diarias.")
    async def daily(ctx: commands.Context) -> None:
        profile, remaining = economy.claim_daily(ctx.author.id)
        if remaining:
            await ctx.send(f"Voce ja pegou o daily. Tente de novo em `{_format_remaining(remaining)}`.")
            return

        await ctx.send(
            f"Daily coletado! Voce recebeu pelo menos `{DAILY_AMOUNT}` moedas, com bonus de streak. "
            f"Saldo atual: `{profile.balance}` | Streak: `{profile.daily_streak}`"
        )

    @bot.hybrid_command(name="pagar", aliases=["pay"], description="Transfere moedas para outro usuario.")
    async def pay(ctx: commands.Context, member: discord.Member, amount: int) -> None:
        try:
            sender, receiver = economy.transfer(ctx.author.id, member.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        await ctx.send(
            f"{ctx.author.mention} pagou `{amount}` moedas para {member.mention}. "
            f"Seu saldo: `{sender.balance}` | Saldo dele(a): `{receiver.balance}`"
        )


def _format_remaining(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"
