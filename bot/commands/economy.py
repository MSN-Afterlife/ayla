import discord
from discord.ext import commands

from bot.config import Settings
from bot.services.economy_service import EconomyService


DAILY_URL_SETTING = "daily_site_url"


def setup_economy_commands(bot: commands.Bot, settings: Settings) -> None:
    economy = EconomyService(settings)

    @bot.hybrid_command(name="saldo", aliases=["balance", "bal", "coins"], description="Mostra seu saldo.")
    async def balance(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        profile = economy.get_profile(target.id)
        await ctx.send(f"Saldo de **{target.display_name}**: `{profile.balance}` moedas | Daily streak: `{profile.daily_streak}`")

    @bot.hybrid_command(name="daily", aliases=["diario"], description="Abre o site para coletar suas moedas diarias.")
    async def daily(ctx: commands.Context) -> None:
        daily_url = economy.get_setting(DAILY_URL_SETTING, settings.daily_site_url)
        embed = discord.Embed(
            title="Daily pelo site",
            description="O resgate diario de moedas agora e feito somente pelo site.",
            color=0x2ECC71,
        )
        embed.add_field(name="Link", value=daily_url, inline=False)
        await ctx.send(embed=embed, view=DailyRedirectView(daily_url))

    @bot.hybrid_command(name="dailyconfig", aliases=["configdaily"], description="Configura o link do daily no site.")
    @commands.has_permissions(manage_guild=True)
    async def daily_config(ctx: commands.Context, url: str | None = None) -> None:
        if not url:
            daily_url = economy.get_setting(DAILY_URL_SETTING, settings.daily_site_url)
            await ctx.send(f"Link atual do daily: {daily_url}")
            return

        if not url.startswith(("http://", "https://")):
            await ctx.send("Envie uma URL valida com `http://` ou `https://`.")
            return

        economy.set_setting(DAILY_URL_SETTING, url)
        await ctx.send(f"Link do daily atualizado para: {url}")

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


class DailyRedirectView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Resgatar daily", url=url))
