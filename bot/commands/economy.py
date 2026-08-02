import random

import discord
from discord.ext import commands

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
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

    @bot.hybrid_command(name="coinflip", aliases=["cf", "caracoroa"], description="Aposta cara ou coroa contra a Ayla.")
    async def coinflip(ctx: commands.Context, amount: int, choice: str) -> None:
        normalized = _normalize_coin_choice(choice)
        if not normalized:
            await ctx.send("Escolha `cara` ou `coroa`.")
            return

        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        result = random.choice(["cara", "coroa"])
        won = normalized == result
        profile = economy.add_balance(ctx.author.id, amount if won else -amount)
        await ctx.send(embed=_bet_embed("Cara ou coroa", ctx.author, amount, won, f"Voce escolheu `{normalized}` e caiu `{result}`.", profile.balance))

    @bot.hybrid_command(name="dado", aliases=["dice"], description="Aposta em um numero de 1 a 6.")
    async def dice(ctx: commands.Context, amount: int, number: int) -> None:
        if number < 1 or number > 6:
            await ctx.send("Escolha um numero entre 1 e 6.")
            return
        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        result = random.randint(1, 6)
        won = number == result
        delta = amount * 5 if won else -amount
        profile = economy.add_balance(ctx.author.id, delta)
        await ctx.send(embed=_bet_embed("Dado", ctx.author, amount, won, f"Voce escolheu `{number}` e o dado caiu `{result}`.", profile.balance, multiplier="6x"))

    @bot.hybrid_command(name="slots", aliases=["slot", "cassino"], description="Aposta em uma maquina de slots.")
    async def slots(ctx: commands.Context, amount: int) -> None:
        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        symbols = ["7", "estrela", "cereja", "sino", "diamante"]
        roll = [random.choice(symbols) for _ in range(3)]
        if len(set(roll)) == 1:
            delta = amount * 4
            won = True
            result_text = "Jackpot! Tres iguais."
        elif len(set(roll)) == 2:
            delta = amount
            won = True
            result_text = "Duas iguais."
        else:
            delta = -amount
            won = False
            result_text = "Sem combinacao."

        profile = economy.add_balance(ctx.author.id, delta)
        await ctx.send(embed=_bet_embed("Slots", ctx.author, amount, won, f"`{' | '.join(roll)}`\n{result_text}", profile.balance))

    @bot.hybrid_command(name="roleta", aliases=["roulette"], description="Aposta em vermelho, preto, par, impar ou numero 0-36.")
    async def roulette(ctx: commands.Context, amount: int, choice: str) -> None:
        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        normalized = choice.lower()
        number = random.randint(0, 36)
        color = "verde" if number == 0 else "vermelho" if number % 2 == 0 else "preto"
        won = False
        payout = amount
        if normalized in {"vermelho", "red"}:
            won = color == "vermelho"
        elif normalized in {"preto", "black"}:
            won = color == "preto"
        elif normalized in {"par", "even"}:
            won = number != 0 and number % 2 == 0
        elif normalized in {"impar", "ímpar", "odd"}:
            won = number % 2 == 1
        elif normalized.isdigit() and 0 <= int(normalized) <= 36:
            won = int(normalized) == number
            payout = amount * 35
        else:
            await ctx.send("Aposte em `vermelho`, `preto`, `par`, `impar` ou um numero de `0` a `36`.")
            return

        profile = economy.add_balance(ctx.author.id, payout if won else -amount)
        await ctx.send(embed=_bet_embed("Roleta", ctx.author, amount, won, f"Caiu `{number}` ({color}). Sua aposta: `{choice}`.", profile.balance))

    @bot.hybrid_command(name="apostar", aliases=["bet"], description="Desafia outro usuario para uma aposta cara ou coroa.")
    async def bet_user(ctx: commands.Context, member: discord.Member, amount: int) -> None:
        if member.bot:
            await ctx.send("Voce nao pode apostar com bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("Voce nao pode apostar consigo mesmo.")
            return
        try:
            _validate_bet(economy, ctx.author.id, amount)
            _validate_bet(economy, member.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        embed = discord.Embed(
            title="Desafio de aposta",
            description=f"{ctx.author.mention} desafiou {member.mention} para uma aposta de `{amount}` moedas.",
            color=0xF1C40F,
        )
        embed.add_field(name="Jogo", value="Cara ou coroa automatico. Quem vencer leva o valor apostado do outro.", inline=False)
        await ctx.send(embed=embed, view=UserBetView(economy, ctx.author, member, amount))


class DailyRedirectView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Resgatar daily", url=url))


class UserBetView(discord.ui.View):
    def __init__(self, economy: EconomyService, challenger: discord.Member | discord.User, opponent: discord.Member | discord.User, amount: int) -> None:
        super().__init__(timeout=120)
        self._economy = economy
        self._challenger = challenger
        self._opponent = opponent
        self._amount = amount

    @discord.ui.button(label="Aceitar", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self._opponent.id:
            await interaction.response.send_message("Apenas a pessoa desafiada pode aceitar.", ephemeral=True)
            return
        try:
            _validate_bet(self._economy, self._challenger.id, self._amount)
            _validate_bet(self._economy, self._opponent.id, self._amount)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        winner, loser = random.sample([self._challenger, self._opponent], 2)
        self._economy.add_balance(winner.id, self._amount)
        self._economy.add_balance(loser.id, -self._amount)
        embed = discord.Embed(
            title="Aposta finalizada",
            description=f"{winner.mention} venceu `{self._amount}` moedas de {loser.mention}.",
            color=0x2ECC71,
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self._opponent.id:
            await interaction.response.send_message("Apenas a pessoa desafiada pode recusar.", ephemeral=True)
            return
        await interaction.response.edit_message(content="A aposta foi recusada.", embed=None, view=None)


def _validate_bet(economy: EconomyService, user_id: int, amount: int) -> None:
    if amount <= 0:
        raise ValueError("A aposta precisa ser maior que zero.")
    if amount > 100000:
        raise ValueError("A aposta maxima por rodada e `100000` moedas.")
    if not economy.can_afford(user_id, amount):
        raise ValueError("Saldo insuficiente para essa aposta.")


def _normalize_coin_choice(choice: str) -> str | None:
    normalized = choice.lower()
    if normalized in {"cara", "head", "heads"}:
        return "cara"
    if normalized in {"coroa", "tail", "tails"}:
        return "coroa"
    return None


def _bet_embed(title: str, user: discord.Member | discord.User, amount: int, won: bool, details: str, balance: int, multiplier: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=details,
        color=0x2ECC71 if won else 0xE74C3C,
    )
    embed.add_field(name="Resultado", value="Vitoria" if won else "Derrota", inline=True)
    embed.add_field(name="Aposta", value=f"`{amount}` moedas", inline=True)
    if multiplier:
        embed.add_field(name="Pagamento", value=multiplier, inline=True)
    embed.add_field(name="Saldo atual", value=f"`{balance}` moedas", inline=False)
    embed.set_footer(text=f"Jogador: {user.display_name}")
    return embed
