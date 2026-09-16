import random

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
from bot.services.economy_service import EconomyService
from bot.services.economy_images import SYMBOLS
from bot.services.economy_images import build_bet_card


DAILY_URL_SETTING = "daily_site_url"
DAILY_DIRECT_CLAIM_SETTING = "daily_direct_claim_enabled"
WINKS_GUILD_ID = 1472950966113276068
WINKS_EMOJI_NAME = "winks"
BOOLEAN_CHOICES = [
    app_commands.Choice(name="Ativar", value="true"),
    app_commands.Choice(name="Desativar", value="false"),
]
COIN_CHOICES = [
    app_commands.Choice(name="Cara", value="cara"),
    app_commands.Choice(name="Coroa", value="coroa"),
]
DICE_CHOICES = [app_commands.Choice(name=str(number), value=number) for number in range(1, 7)]
HIGHLOW_CHOICES = [
    app_commands.Choice(name="Maior", value="maior"),
    app_commands.Choice(name="Menor", value="menor"),
]


def setup_economy_commands(bot: commands.Bot, settings: Settings) -> None:
    economy = EconomyService(settings, write_gate=getattr(bot, "_economy_write_gate", None))

    @bot.hybrid_command(name="saldo", aliases=["balance", "bal", "coins"], description="Mostra seu saldo.")
    async def balance(ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        profile = economy.get_profile(target.id)
        embed = discord.Embed(title="Carteira da Ayla", color=0x7AA7FF)
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.add_field(name="Saldo", value=_currency(profile.balance, bot), inline=True)
        embed.add_field(name="Daily streak", value=f"{profile.daily_streak} dia(s)", inline=True)
        await ctx.send(embed=embed)

    @bot.hybrid_command(name="daily", aliases=["diario"], description="Coleta seus winks diarios ou abre o site configurado.")
    async def daily(ctx: commands.Context) -> None:
        if _get_daily_direct_claim_enabled(economy, settings):
            claim = economy.claim_daily(ctx.author.id)
            profile = claim.profile
            if claim.remaining_seconds:
                embed = discord.Embed(
                    title="Daily ja resgatado",
                    description=f"Voce ja pegou o daily hoje. Tente de novo em {_format_remaining(claim.remaining_seconds)}.",
                    color=0xF1C40F,
                )
                embed.add_field(name="Saldo atual", value=_currency(profile.balance, bot), inline=True)
                embed.add_field(name="Daily streak", value=f"{profile.daily_streak} dia(s)", inline=True)
                await ctx.send(embed=embed)
                return

            embed = discord.Embed(
                title="Daily resgatado",
                description=f"Voce recebeu {_currency(claim.amount or DAILY_AMOUNT, bot)}.",
                color=0x2ECC71,
            )
            if claim.bonus:
                embed.add_field(name="Bonus de streak", value=_currency(claim.bonus, bot), inline=True)
            embed.add_field(name="Saldo atual", value=_currency(profile.balance, bot), inline=True)
            embed.add_field(name="Daily streak", value=f"{profile.daily_streak} dia(s)", inline=True)
            await ctx.send(embed=embed)
            return

        daily_url = economy.get_setting(DAILY_URL_SETTING, settings.daily_site_url)
        embed = discord.Embed(
            title="Daily pelo site",
            description=f"Resgate seus winks diarios pelo site. Valor base: {_currency(DAILY_AMOUNT, bot)}.",
            color=0x7AA7FF,
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

    @bot.hybrid_command(name="dailybot", aliases=["dailymodo", "dailyinterruptor"], description="Liga ou desliga o resgate direto do daily pelo Discord.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.choices(enabled=BOOLEAN_CHOICES)
    async def daily_bot(ctx: commands.Context, enabled: str | None = None) -> None:
        if enabled is None:
            current = _get_daily_direct_claim_enabled(economy, settings)
            mode = "direto pelo `/daily`" if current else "redirecionando para o site"
            await ctx.send(f"Modo atual do daily: **{mode}**. Use `{settings.command_prefix}dailybot true` ou `{settings.command_prefix}dailybot false`.")
            return

        parsed = _parse_bool(enabled)
        if parsed is None:
            await ctx.send("Use `true` para resgatar direto pelo `/daily` ou `false` para voltar a direcionar para o site.")
            return

        economy.set_setting(DAILY_DIRECT_CLAIM_SETTING, "true" if parsed else "false")
        if parsed:
            await ctx.send("Daily direto ativado. Agora os usuarios conseguem resgatar com `/daily`, sem passar pelo site.")
        else:
            await ctx.send("Daily direto desativado. Agora `/daily` volta a direcionar para o site.")

    @bot.command(name="addmoney")
    @commands.has_permissions(administrator=True)
    async def add_money(ctx: commands.Context, member: discord.Member, amount: int) -> None:
        try:
            profile = economy.add_balance(member.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        embed = discord.Embed(title="Winks adicionados", color=0x7AA7FF)
        embed.add_field(name="Usuario", value=member.mention, inline=True)
        embed.add_field(name="Valor", value=_currency(amount, bot), inline=True)
        embed.add_field(name="Saldo atual", value=_currency(profile.balance, bot), inline=False)
        await ctx.send(embed=embed)

    @bot.hybrid_command(name="pagar", aliases=["pay"], description="Transfere winks para outro usuario.")
    async def pay(ctx: commands.Context, member: discord.Member, amount: int) -> None:
        try:
            sender, receiver = economy.transfer(ctx.author.id, member.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        embed = discord.Embed(title="Transferencia concluida", color=0x7AA7FF)
        embed.add_field(name="De", value=ctx.author.mention, inline=True)
        embed.add_field(name="Para", value=member.mention, inline=True)
        embed.add_field(name="Valor", value=_currency(amount, bot), inline=True)
        embed.add_field(name="Saldo de quem enviou", value=_currency(sender.balance, bot), inline=True)
        embed.add_field(name="Saldo de quem recebeu", value=_currency(receiver.balance, bot), inline=True)
        await ctx.send(embed=embed)

    @bot.hybrid_command(name="coinflip", aliases=["cf", "caracoroa"], description="Aposta cara ou coroa contra a Ayla.")
    @app_commands.choices(choice=COIN_CHOICES)
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
        await _send_bet_result(
            ctx,
            "Cara ou coroa",
            ctx.author,
            amount,
            won,
            f"Voce escolheu `{normalized}` e caiu `{result}`.",
            profile.balance,
            [normalized.upper(), result.upper()],
            delta=amount if won else -amount,
        )

    @bot.hybrid_command(name="dado", aliases=["dice"], description="Aposta em um numero de 1 a 6.")
    @app_commands.choices(number=DICE_CHOICES)
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
        await _send_bet_result(ctx, "Dado", ctx.author, amount, won, f"Voce escolheu `{number}` e o dado caiu `{result}`.", profile.balance, [str(number), str(result)], multiplier="6x", delta=delta)

    @bot.hybrid_command(name="slots", aliases=["slot", "cassino"], description="Aposta em uma maquina de slots.")
    async def slots(ctx: commands.Context, amount: int) -> None:
        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        symbols = list(SYMBOLS)
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
        visual_roll = [SYMBOLS[symbol] for symbol in roll]
        await _send_bet_result(ctx, "Slots", ctx.author, amount, won, f"`{' | '.join(roll)}`\n{result_text}", profile.balance, visual_roll, delta=delta)

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
        await _send_bet_result(ctx, "Roleta", ctx.author, amount, won, f"Caiu `{number}` ({color}). Sua aposta: `{choice}`.", profile.balance, [choice.upper(), str(number)], delta=payout if won else -amount)

    @bot.hybrid_command(name="highlow", aliases=["maioroumenor", "hl"], description="Aposte se a proxima carta sera maior ou menor.")
    @app_commands.choices(choice=HIGHLOW_CHOICES)
    async def highlow(ctx: commands.Context, amount: int, choice: str) -> None:
        normalized = _normalize_highlow_choice(choice)
        if not normalized:
            await ctx.send("Escolha `maior` ou `menor`.")
            return

        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        first = random.randint(1, 13)
        second = random.randint(1, 13)
        while second == first:
            second = random.randint(1, 13)

        won = second > first if normalized == "maior" else second < first
        profile = economy.add_balance(ctx.author.id, amount if won else -amount)
        await _send_bet_result(
            ctx,
            "Maior ou menor",
            ctx.author,
            amount,
            won,
            f"Carta inicial `{_card_label(first)}`. Voce apostou em `{normalized}` e saiu `{_card_label(second)}`.",
            profile.balance,
            [_card_label(first), _card_label(second)],
            delta=amount if won else -amount,
        )

    @bot.hybrid_command(name="21", aliases=["blackjack", "bj"], description="Joga 21 contra a Ayla com botoes de pedir/parar.")
    async def blackjack(ctx: commands.Context, amount: int) -> None:
        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        view = BlackjackView(economy, ctx.bot, ctx.author, amount)
        await ctx.send(embed=view.embed(), view=view)

    @bot.hybrid_command(name="cartaalta", aliases=["war", "guerra"], description="Aposte na maior carta contra a Ayla.")
    async def high_card(ctx: commands.Context, amount: int) -> None:
        try:
            _validate_bet(economy, ctx.author.id, amount)
        except ValueError as error:
            await ctx.send(str(error))
            return

        deck = _new_deck()
        player_card = deck.pop()
        ayla_card = deck.pop()
        while _card_rank_value(player_card) == _card_rank_value(ayla_card):
            ayla_card = deck.pop()

        won = _card_rank_value(player_card) > _card_rank_value(ayla_card)
        profile = economy.add_balance(ctx.author.id, amount if won else -amount)
        await _send_bet_result(
            ctx,
            "Carta alta",
            ctx.author,
            amount,
            won,
            f"Voce tirou `{_display_card(player_card)}`. Ayla tirou `{_display_card(ayla_card)}`.",
            profile.balance,
            [_display_card(player_card), _display_card(ayla_card)],
            delta=amount if won else -amount,
        )

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
            description=f"{ctx.author.mention} desafiou {member.mention} para uma aposta de {_currency_inline(amount, bot)}.",
            color=0xF1C40F,
        )
        embed.add_field(name="Jogo", value="Cara ou coroa automatico. Quem vencer leva o valor apostado do outro.", inline=False)
        await ctx.send(embed=embed, view=UserBetView(economy, bot, ctx.author, member, amount))


class DailyRedirectView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=180)
        self.add_item(discord.ui.Button(label="Resgatar daily", url=url))


class UserBetView(discord.ui.View):
    def __init__(self, economy: EconomyService, bot: commands.Bot, challenger: discord.Member | discord.User, opponent: discord.Member | discord.User, amount: int) -> None:
        super().__init__(timeout=120)
        self._economy = economy
        self._bot = bot
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
            description=f"{winner.mention} venceu {_currency_inline(self._amount, self._bot)} de {loser.mention}.",
            color=0x2ECC71,
        )
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self._opponent.id:
            await interaction.response.send_message("Apenas a pessoa desafiada pode recusar.", ephemeral=True)
            return
        await interaction.response.edit_message(content="A aposta foi recusada.", embed=None, view=None)


class BlackjackView(discord.ui.View):
    def __init__(self, economy: EconomyService, bot: commands.Bot, player: discord.Member | discord.User, amount: int) -> None:
        super().__init__(timeout=120)
        self._economy = economy
        self._bot = bot
        self._player = player
        self._amount = amount
        self._deck = _new_deck()
        self._player_hand = [self._draw(), self._draw()]
        self._dealer_hand = [self._draw(), self._draw()]
        self._finished = False

    def embed(self, reveal_dealer: bool = False, result: str | None = None, balance: int | None = None) -> discord.Embed:
        player_total = _hand_value(self._player_hand)
        dealer_cards = self._dealer_hand if reveal_dealer else self._dealer_hand[:1]
        dealer_total = _hand_value(dealer_cards)
        color = 0x7AA7FF if result is None else 0x2ECC71 if result == "Vitoria" else 0xE74C3C if result == "Derrota" else 0xF1C40F
        embed = discord.Embed(title="21 da Ayla", color=color)
        embed.set_author(name=self._player.display_name, icon_url=self._player.display_avatar.url)
        embed.add_field(name="Sua mao", value=f"{_format_hand(self._player_hand)}\nTotal: `{player_total}`", inline=False)
        hidden = "" if reveal_dealer else " + ??"
        embed.add_field(name="Mao da Ayla", value=f"{_format_hand(dealer_cards)}{hidden}\nTotal visivel: `{dealer_total}`", inline=False)
        embed.add_field(name="Aposta", value=_currency(self._amount, self._bot), inline=True)
        if result:
            embed.add_field(name="Resultado", value=result, inline=True)
        if balance is not None:
            embed.add_field(name="Saldo atual", value=_currency(balance, self._bot), inline=True)
        return embed

    @discord.ui.button(label="Pedir", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return

        self._player_hand.append(self._draw())
        if _hand_value(self._player_hand) > 21:
            await self._finish(interaction, "Derrota", -self._amount, "Voce estourou.")
            return

        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Parar", style=discord.ButtonStyle.success)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return

        while _hand_value(self._dealer_hand) < 17:
            self._dealer_hand.append(self._draw())

        player_total = _hand_value(self._player_hand)
        dealer_total = _hand_value(self._dealer_hand)
        if dealer_total > 21 or player_total > dealer_total:
            await self._finish(interaction, "Vitoria", self._amount, "Voce venceu a Ayla.")
        elif player_total < dealer_total:
            await self._finish(interaction, "Derrota", -self._amount, "A Ayla venceu essa.")
        else:
            await self._finish(interaction, "Empate", 0, "Ninguem perdeu winks.")

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._player.id:
            await interaction.response.send_message("Essa rodada pertence a outra pessoa.", ephemeral=True)
            return False
        if self._finished:
            await interaction.response.send_message("Essa rodada ja terminou.", ephemeral=True)
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, result: str, delta: int, details: str) -> None:
        self._finished = True
        for child in self.children:
            child.disabled = True

        if delta < 0:
            try:
                _validate_bet(self._economy, self._player.id, self._amount)
            except ValueError as error:
                await interaction.response.edit_message(content=str(error), embed=None, view=None)
                return

        profile = self._economy.add_balance(self._player.id, delta) if delta else self._economy.get_profile(self._player.id)
        file = build_bet_card(
            "21 da Ayla",
            details,
            [_format_hand(self._player_hand), _format_hand(self._dealer_hand)],
            won=delta >= 0,
            amount=self._amount,
            balance=profile.balance,
            delta=delta,
        )
        embed = self.embed(reveal_dealer=True, result=result, balance=profile.balance)
        embed.set_image(url="attachment://aposta.png")
        await interaction.response.edit_message(embed=embed, attachments=[file], view=None)

    def _draw(self) -> str:
        return self._deck.pop()

    async def on_timeout(self) -> None:
        self._finished = True
        for child in self.children:
            child.disabled = True


def _validate_bet(economy: EconomyService, user_id: int, amount: int) -> None:
    if amount <= 0:
        raise ValueError("A aposta precisa ser maior que zero.")
    if amount > 100000:
        raise ValueError(f"A aposta maxima por rodada e {_currency_inline(100000)}.")
    if not economy.can_afford(user_id, amount):
        raise ValueError("Saldo insuficiente para essa aposta.")


def _get_daily_direct_claim_enabled(economy: EconomyService, settings: Settings) -> bool:
    default = "true" if settings.daily_direct_claim_enabled else "false"
    return _parse_bool(economy.get_setting(DAILY_DIRECT_CLAIM_SETTING, default)) is True


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "sim", "s", "on", "ligado", "ativado"}:
        return True
    if normalized in {"0", "false", "no", "n", "nao", "não", "off", "desligado", "desativado"}:
        return False
    return None


def _format_remaining(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


def _normalize_coin_choice(choice: str) -> str | None:
    normalized = choice.lower()
    if normalized in {"cara", "head", "heads"}:
        return "cara"
    if normalized in {"coroa", "tail", "tails"}:
        return "coroa"
    return None


def _normalize_highlow_choice(choice: str) -> str | None:
    normalized = choice.lower()
    if normalized in {"maior", "higher", "high", "alto"}:
        return "maior"
    if normalized in {"menor", "lower", "low", "baixo"}:
        return "menor"
    return None


def _card_label(value: int) -> str:
    labels = {1: "A", 11: "J", 12: "Q", 13: "K"}
    return labels.get(value, str(value))


def _new_deck() -> list[str]:
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    suits = ["S", "H", "D", "C"]
    deck = [f"{rank}{suit}" for suit in suits for rank in ranks]
    random.shuffle(deck)
    return deck


def _format_hand(hand: list[str]) -> str:
    return " ".join(_display_card(card) for card in hand)


def _display_card(card: str) -> str:
    return card[:-1]


def _card_rank_value(card: str) -> int:
    rank = card[:-1]
    if rank == "A":
        return 14
    if rank == "K":
        return 13
    if rank == "Q":
        return 12
    if rank == "J":
        return 11
    return int(rank)


def _hand_value(hand: list[str]) -> int:
    total = 0
    aces = 0
    for card in hand:
        rank = card[:-1]
        if rank == "A":
            aces += 1
            total += 11
        elif rank in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(rank)

    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


async def _send_bet_result(
    ctx: commands.Context,
    title: str,
    user: discord.Member | discord.User,
    amount: int,
    won: bool,
    details: str,
    balance: int,
    values: list[str],
    multiplier: str | None = None,
    delta: int | None = None,
) -> None:
    file = build_bet_card(title, details, values, won=won, amount=amount, balance=balance, delta=delta)
    embed = _bet_embed(title, user, amount, won, details, balance, bot=ctx.bot, multiplier=multiplier)
    embed.set_image(url="attachment://aposta.png")
    await ctx.send(embed=embed, file=file)


def _bet_embed(title: str, user: discord.Member | discord.User, amount: int, won: bool, details: str, balance: int, bot: commands.Bot | None = None, multiplier: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=details,
        color=0x2ECC71 if won else 0xE74C3C,
    )
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    embed.add_field(name="Resultado", value="Vitoria" if won else "Derrota", inline=True)
    embed.add_field(name="Aposta", value=_currency(amount, bot), inline=True)
    if multiplier:
        embed.add_field(name="Pagamento", value=multiplier, inline=True)
    embed.add_field(name="Saldo atual", value=_currency(balance, bot), inline=False)
    return embed


def _currency(amount: int, bot: commands.Bot | None = None) -> str:
    emoji = _currency_emoji(bot)
    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}`{amount}` {_currency_name(amount)}"


def _currency_inline(amount: int, bot: commands.Bot | None = None) -> str:
    return _currency(amount, bot)


def _currency_name(amount: int) -> str:
    return "wink" if abs(amount) == 1 else "winks"


def _currency_emoji(bot: commands.Bot | None) -> str | None:
    if bot is None:
        return None
    guild = bot.get_guild(WINKS_GUILD_ID)
    if guild is None:
        return None
    emoji = discord.utils.get(guild.emojis, name=WINKS_EMOJI_NAME)
    return str(emoji) if emoji else None
