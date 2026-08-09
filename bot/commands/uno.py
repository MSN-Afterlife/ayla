import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands


COLORS = {
    "vermelho": 0xE74C3C,
    "azul": 0x3498DB,
    "verde": 0x2ECC71,
    "amarelo": 0xF1C40F,
}
COLOR_ALIASES = {
    "red": "vermelho",
    "blue": "azul",
    "green": "verde",
    "yellow": "amarelo",
    "v": "vermelho",
    "a": "azul",
    "g": "verde",
    "y": "amarelo",
}
VALUES = [str(number) for number in range(10)] + ["bloqueio", "reverso", "+2"]
ACTION_VALUES = {"bloqueio", "reverso", "+2"}
WILD_VALUES = {"coringa", "+4"}
UNO_COLOR_CHOICES = [
    app_commands.Choice(name="Vermelho", value="vermelho"),
    app_commands.Choice(name="Azul", value="azul"),
    app_commands.Choice(name="Verde", value="verde"),
    app_commands.Choice(name="Amarelo", value="amarelo"),
]


@dataclass(frozen=True)
class UnoCard:
    color: str | None
    value: str

    @property
    def is_wild(self) -> bool:
        return self.value in WILD_VALUES

    def label(self) -> str:
        if self.is_wild:
            return self.value.upper()
        return f"{self.color} {self.value}"


@dataclass
class UnoPlayer:
    member: discord.Member | discord.User
    hand: list[UnoCard] = field(default_factory=list)
    said_uno: bool = False


class UnoGame:
    def __init__(self, channel_id: int, host: discord.Member | discord.User) -> None:
        self.channel_id = channel_id
        self.host_id = host.id
        self.players = [UnoPlayer(host)]
        self.deck = _new_uno_deck()
        self.discard: list[UnoCard] = []
        self.current_color: str | None = None
        self.turn_index = 0
        self.direction = 1
        self.started = False
        self.awaiting_draw = False

    @property
    def current_player(self) -> UnoPlayer:
        return self.players[self.turn_index]

    @property
    def top_card(self) -> UnoCard:
        return self.discard[-1]

    def add_player(self, member: discord.Member | discord.User) -> bool:
        if self.started or any(player.member.id == member.id for player in self.players):
            return False
        self.players.append(UnoPlayer(member))
        return True

    def start(self) -> None:
        random.shuffle(self.deck)
        for _ in range(7):
            for player in self.players:
                player.hand.append(self.draw_one())

        top = self.draw_one()
        while top.is_wild or top.value in ACTION_VALUES:
            self.deck.insert(0, top)
            random.shuffle(self.deck)
            top = self.draw_one()

        self.discard.append(top)
        self.current_color = top.color
        self.started = True

    def draw_one(self) -> UnoCard:
        if not self.deck:
            top = self.discard.pop()
            self.deck = self.discard
            self.discard = [top]
            random.shuffle(self.deck)
        return self.deck.pop()

    def can_play(self, card: UnoCard) -> bool:
        top = self.top_card
        return card.is_wild or card.color == self.current_color or card.value == top.value

    def play(self, hand_index: int, color: str | None) -> tuple[bool, str, UnoCard | None]:
        if hand_index < 0 or hand_index >= len(self.current_player.hand):
            return False, "Indice de carta invalido.", None

        card = self.current_player.hand[hand_index]
        if not self.can_play(card):
            return False, "Essa carta nao combina com a cor ou valor atual.", None
        if card.is_wild and not color:
            return False, "Escolha uma cor: `vermelho`, `azul`, `verde` ou `amarelo`.", None
        if color and color not in COLORS:
            return False, "Cor invalida. Use `vermelho`, `azul`, `verde` ou `amarelo`.", None

        self.current_player.hand.pop(hand_index)
        self.discard.append(card)
        self.current_color = color if card.is_wild else card.color
        self.awaiting_draw = False
        return True, self.apply_card_effect(card), card

    def apply_card_effect(self, card: UnoCard) -> str:
        if card.value == "reverso":
            self.direction *= -1
            if len(self.players) == 2:
                self.advance()
            self.advance()
            return "Ordem invertida."
        if card.value == "bloqueio":
            skipped = self.next_player().member.display_name
            self.advance(2)
            return f"{skipped} foi bloqueado."
        if card.value == "+2":
            target = self.next_player()
            target.hand.extend([self.draw_one(), self.draw_one()])
            self.advance(2)
            return f"{target.member.display_name} comprou 2 cartas e perdeu a vez."
        if card.value == "+4":
            target = self.next_player()
            target.hand.extend([self.draw_one(), self.draw_one(), self.draw_one(), self.draw_one()])
            self.advance(2)
            return f"{target.member.display_name} comprou 4 cartas e perdeu a vez."

        self.advance()
        return "Carta jogada."

    def next_player(self) -> UnoPlayer:
        return self.players[(self.turn_index + self.direction) % len(self.players)]

    def advance(self, steps: int = 1) -> None:
        self.turn_index = (self.turn_index + self.direction * steps) % len(self.players)
        self.current_player.said_uno = False

    def draw_for_current(self) -> UnoCard:
        card = self.draw_one()
        self.current_player.hand.append(card)
        self.awaiting_draw = True
        return card

    def pass_turn(self) -> None:
        self.awaiting_draw = False
        self.advance()

    def winner(self) -> UnoPlayer | None:
        return next((player for player in self.players if not player.hand), None)


def setup_uno_commands(bot: commands.Bot) -> None:
    games: dict[int, UnoGame] = {}
    uno_slash = app_commands.Group(name="uno", description="Joga UNO da Ayla com 2+ jogadores.")

    @bot.group(name="uno", invoke_without_command=True)
    async def uno(ctx: commands.Context) -> None:
        await ctx.send(
            "UNO: `a!uno criar`, `entrar`, `iniciar`, `mao`, `jogar <numero> [cor]`, "
            "`comprar`, `passar`, `uno`, `mesa`, `cancelar`."
        )

    @uno.command(name="criar")
    async def uno_create(ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("UNO precisa ser criado dentro de um servidor.")
            return
        if ctx.channel.id in games:
            await ctx.send("Ja existe uma mesa de UNO neste canal.")
            return

        games[ctx.channel.id] = UnoGame(ctx.channel.id, ctx.author)
        await ctx.send(f"Mesa de UNO criada por {ctx.author.mention}. Use `a!uno entrar` para participar.")

    @uno.command(name="entrar")
    async def uno_join(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game:
            await ctx.send("Nao tem mesa de UNO neste canal. Use `a!uno criar`.")
            return
        if game.add_player(ctx.author):
            await ctx.send(f"{ctx.author.mention} entrou na mesa. Jogadores: `{len(game.players)}`.")
            return
        await ctx.send("Voce ja esta na mesa ou a partida ja comecou.")

    @uno.command(name="iniciar")
    async def uno_start(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game:
            await ctx.send("Nao tem mesa de UNO neste canal.")
            return
        if ctx.author.id != game.host_id:
            await ctx.send("Apenas quem criou a mesa pode iniciar.")
            return
        if len(game.players) < 2:
            await ctx.send("UNO precisa de pelo menos 2 jogadores.")
            return

        game.start()
        failed = await _dm_all_hands(game)
        embed = _table_embed(game, "Partida iniciada.")
        message = "Nao consegui mandar DM para: " + ", ".join(failed) if failed else None
        await ctx.send(content=message, embed=embed)

    @uno.command(name="mao")
    async def uno_hand(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        player = _find_player(game, ctx.author.id) if game else None
        if not game or not player:
            await ctx.send("Voce nao esta em uma mesa de UNO neste canal.")
            return
        if await _send_hand(player, game):
            await ctx.send("Mandei sua mao no privado.")
        else:
            await ctx.send("Nao consegui te mandar DM. Abra suas mensagens privadas para a Ayla.")

    @uno.command(name="mesa")
    async def uno_table(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started:
            await ctx.send("Nao tem partida de UNO em andamento neste canal.")
            return
        await ctx.send(embed=_table_embed(game))

    @uno.command(name="jogar")
    async def uno_play(ctx: commands.Context, card_number: int, color: str | None = None) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started:
            await ctx.send("Nao tem partida de UNO em andamento neste canal.")
            return
        if ctx.author.id != game.current_player.member.id:
            await ctx.send(f"Agora e a vez de {game.current_player.member.mention}.")
            return

        normalized_color = _normalize_color(color)
        acting_player = game.current_player
        ok, message, card = game.play(card_number - 1, normalized_color)
        if not ok:
            await ctx.send(message)
            return

        await _send_hand(acting_player, game)
        winner = game.winner()
        if winner:
            games.pop(ctx.channel.id, None)
            await ctx.send(embed=_winner_embed(winner, card))
            return

        if len(acting_player.hand) == 1 and not acting_player.said_uno:
            acting_player.hand.extend([game.draw_one(), game.draw_one()])
            message += " Nao falou UNO com uma carta: comprou 2."
            await _send_hand(acting_player, game)

        await _send_hand(game.current_player, game)
        await ctx.send(embed=_table_embed(game, message))

    @uno.command(name="comprar")
    async def uno_draw(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started:
            await ctx.send("Nao tem partida de UNO em andamento neste canal.")
            return
        if ctx.author.id != game.current_player.member.id:
            await ctx.send(f"Agora e a vez de {game.current_player.member.mention}.")
            return
        card = game.draw_for_current()
        await _send_hand(game.current_player, game)
        await ctx.send(f"{ctx.author.mention} comprou uma carta. Se nao for jogar, use `a!uno passar`.")

    @uno.command(name="passar")
    async def uno_pass(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started:
            await ctx.send("Nao tem partida de UNO em andamento neste canal.")
            return
        if ctx.author.id != game.current_player.member.id:
            await ctx.send(f"Agora e a vez de {game.current_player.member.mention}.")
            return
        if not game.awaiting_draw:
            await ctx.send("Voce precisa comprar antes de passar.")
            return
        game.pass_turn()
        await _send_hand(game.current_player, game)
        await ctx.send(embed=_table_embed(game, "Vez passada."))

    @uno.command(name="uno")
    async def uno_call(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        player = _find_player(game, ctx.author.id) if game else None
        if not game or not player:
            await ctx.send("Voce nao esta nessa partida.")
            return
        if len(player.hand) != 1:
            await ctx.send("Voce so pode falar UNO quando esta com uma carta.")
            return
        player.said_uno = True
        await ctx.send(f"{ctx.author.mention} falou UNO.")

    @uno.command(name="cancelar")
    async def uno_cancel(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game:
            await ctx.send("Nao tem mesa de UNO neste canal.")
            return
        can_cancel = ctx.author.id == game.host_id or getattr(ctx.author.guild_permissions, "manage_guild", False)
        if not can_cancel:
            await ctx.send("Apenas quem criou a mesa ou alguem com permissao de gerenciar servidor pode cancelar.")
            return
        games.pop(ctx.channel.id, None)
        await ctx.send("Mesa de UNO cancelada.")

    @uno_slash.command(name="criar", description="Cria uma mesa de UNO neste canal.")
    async def uno_create_slash(interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("UNO precisa ser criado dentro de um servidor.", ephemeral=True)
            return
        if interaction.channel.id in games:
            await interaction.response.send_message("Ja existe uma mesa de UNO neste canal.", ephemeral=True)
            return

        games[interaction.channel.id] = UnoGame(interaction.channel.id, interaction.user)
        await interaction.response.send_message(f"Mesa de UNO criada por {interaction.user.mention}. Use `/uno entrar` para participar.")

    @uno_slash.command(name="entrar", description="Entra na mesa de UNO deste canal.")
    async def uno_join_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game:
            await interaction.response.send_message("Nao tem mesa de UNO neste canal. Use `/uno criar`.", ephemeral=True)
            return
        if game.add_player(interaction.user):
            await interaction.response.send_message(f"{interaction.user.mention} entrou na mesa. Jogadores: `{len(game.players)}`.")
            return
        await interaction.response.send_message("Voce ja esta na mesa ou a partida ja comecou.", ephemeral=True)

    @uno_slash.command(name="iniciar", description="Inicia a partida e envia as cartas por DM.")
    async def uno_start_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game:
            await interaction.response.send_message("Nao tem mesa de UNO neste canal.", ephemeral=True)
            return
        if interaction.user.id != game.host_id:
            await interaction.response.send_message("Apenas quem criou a mesa pode iniciar.", ephemeral=True)
            return
        if len(game.players) < 2:
            await interaction.response.send_message("UNO precisa de pelo menos 2 jogadores.", ephemeral=True)
            return

        game.start()
        failed = await _dm_all_hands(game)
        message = "Nao consegui mandar DM para: " + ", ".join(failed) if failed else None
        await interaction.response.send_message(content=message, embed=_table_embed(game, "Partida iniciada."))

    @uno_slash.command(name="mao", description="Reenvia sua mao por DM.")
    async def uno_hand_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        player = _find_player(game, interaction.user.id) if game else None
        if not game or not player:
            await interaction.response.send_message("Voce nao esta em uma mesa de UNO neste canal.", ephemeral=True)
            return
        if await _send_hand(player, game):
            await interaction.response.send_message("Mandei sua mao no privado.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui te mandar DM. Abra suas mensagens privadas para a Ayla.", ephemeral=True)

    @uno_slash.command(name="mesa", description="Mostra o estado atual da mesa.")
    async def uno_table_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game or not game.started:
            await interaction.response.send_message("Nao tem partida de UNO em andamento neste canal.", ephemeral=True)
            return
        await interaction.response.send_message(embed=_table_embed(game))

    @uno_slash.command(name="jogar", description="Joga uma carta da sua mao.")
    @app_commands.describe(numero="Numero da carta na sua mao.", cor="Cor para coringa ou +4.")
    @app_commands.choices(cor=UNO_COLOR_CHOICES)
    async def uno_play_slash(interaction: discord.Interaction, numero: int, cor: str | None = None) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game or not game.started:
            await interaction.response.send_message("Nao tem partida de UNO em andamento neste canal.", ephemeral=True)
            return
        if interaction.user.id != game.current_player.member.id:
            await interaction.response.send_message(f"Agora e a vez de {game.current_player.member.mention}.", ephemeral=True)
            return

        normalized_color = _normalize_color(cor)
        acting_player = game.current_player
        ok, message, card = game.play(numero - 1, normalized_color)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return

        await _send_hand(acting_player, game)
        winner = game.winner()
        if winner:
            games.pop(interaction.channel.id, None)
            await interaction.response.send_message(embed=_winner_embed(winner, card))
            return

        if len(acting_player.hand) == 1 and not acting_player.said_uno:
            acting_player.hand.extend([game.draw_one(), game.draw_one()])
            message += " Nao falou UNO com uma carta: comprou 2."
            await _send_hand(acting_player, game)

        await _send_hand(game.current_player, game)
        await interaction.response.send_message(embed=_table_embed(game, message))

    @uno_slash.command(name="comprar", description="Compra uma carta.")
    async def uno_draw_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game or not game.started:
            await interaction.response.send_message("Nao tem partida de UNO em andamento neste canal.", ephemeral=True)
            return
        if interaction.user.id != game.current_player.member.id:
            await interaction.response.send_message(f"Agora e a vez de {game.current_player.member.mention}.", ephemeral=True)
            return
        game.draw_for_current()
        await _send_hand(game.current_player, game)
        await interaction.response.send_message(f"{interaction.user.mention} comprou uma carta. Se nao for jogar, use `/uno passar`.")

    @uno_slash.command(name="passar", description="Passa a vez depois de comprar.")
    async def uno_pass_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game or not game.started:
            await interaction.response.send_message("Nao tem partida de UNO em andamento neste canal.", ephemeral=True)
            return
        if interaction.user.id != game.current_player.member.id:
            await interaction.response.send_message(f"Agora e a vez de {game.current_player.member.mention}.", ephemeral=True)
            return
        if not game.awaiting_draw:
            await interaction.response.send_message("Voce precisa comprar antes de passar.", ephemeral=True)
            return
        game.pass_turn()
        await _send_hand(game.current_player, game)
        await interaction.response.send_message(embed=_table_embed(game, "Vez passada."))

    @uno_slash.command(name="uno", description="Declara UNO quando voce esta com uma carta.")
    async def uno_call_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        player = _find_player(game, interaction.user.id) if game else None
        if not game or not player:
            await interaction.response.send_message("Voce nao esta nessa partida.", ephemeral=True)
            return
        if len(player.hand) != 1:
            await interaction.response.send_message("Voce so pode falar UNO quando esta com uma carta.", ephemeral=True)
            return
        player.said_uno = True
        await interaction.response.send_message(f"{interaction.user.mention} falou UNO.")

    @uno_slash.command(name="cancelar", description="Cancela a mesa de UNO deste canal.")
    async def uno_cancel_slash(interaction: discord.Interaction) -> None:
        if not interaction.channel:
            await interaction.response.send_message("Use este comando em um canal.", ephemeral=True)
            return
        game = games.get(interaction.channel.id)
        if not game:
            await interaction.response.send_message("Nao tem mesa de UNO neste canal.", ephemeral=True)
            return
        permissions = getattr(interaction.user, "guild_permissions", None)
        can_cancel = interaction.user.id == game.host_id or bool(permissions and permissions.manage_guild)
        if not can_cancel:
            await interaction.response.send_message("Apenas quem criou a mesa ou alguem com permissao de gerenciar servidor pode cancelar.", ephemeral=True)
            return
        games.pop(interaction.channel.id, None)
        await interaction.response.send_message("Mesa de UNO cancelada.")

    bot.tree.add_command(uno_slash)


async def _dm_all_hands(game: UnoGame) -> list[str]:
    failed = []
    for player in game.players:
        if not await _send_hand(player, game):
            failed.append(player.member.display_name)
    return failed


async def _send_hand(player: UnoPlayer, game: UnoGame) -> bool:
    lines = [f"Topo: {_top_label(game)} | Vez: {game.current_player.member.display_name}", ""]
    lines.extend(f"`{index}.` {card.label()}" for index, card in enumerate(player.hand, start=1))
    lines.append("")
    lines.append("Jogue no canal: `a!uno jogar <numero> [cor]`. Para coringa/+4, informe a cor.")
    try:
        await player.member.send("\n".join(lines))
        return True
    except discord.HTTPException:
        return False


def _table_embed(game: UnoGame, note: str | None = None) -> discord.Embed:
    embed = discord.Embed(title="UNO da Ayla", description=note, color=COLORS.get(game.current_color or "azul", 0x7AA7FF))
    embed.add_field(name="Topo", value=_top_label(game), inline=True)
    embed.add_field(name="Cor atual", value=game.current_color or "nenhuma", inline=True)
    embed.add_field(name="Vez", value=game.current_player.member.mention, inline=True)
    status = "\n".join(f"{player.member.display_name}: `{len(player.hand)}` carta(s)" for player in game.players)
    embed.add_field(name="Jogadores", value=status, inline=False)
    return embed


def _winner_embed(winner: UnoPlayer, card: UnoCard | None) -> discord.Embed:
    embed = discord.Embed(title="UNO finalizado", color=0x2ECC71)
    embed.description = f"{winner.member.mention} venceu a partida."
    if card:
        embed.add_field(name="Ultima carta", value=card.label(), inline=True)
    return embed


def _find_player(game: UnoGame | None, user_id: int) -> UnoPlayer | None:
    if not game:
        return None
    return next((player for player in game.players if player.member.id == user_id), None)


def _top_label(game: UnoGame) -> str:
    return f"{game.top_card.label()} ({game.current_color})"


def _normalize_color(color: str | None) -> str | None:
    if not color:
        return None
    normalized = color.lower()
    return COLOR_ALIASES.get(normalized, normalized)


def _new_uno_deck() -> list[UnoCard]:
    deck: list[UnoCard] = []
    for color in COLORS:
        deck.append(UnoCard(color, "0"))
        for value in VALUES[1:]:
            deck.append(UnoCard(color, value))
            deck.append(UnoCard(color, value))
    for _ in range(4):
        deck.append(UnoCard(None, "coringa"))
        deck.append(UnoCard(None, "+4"))
    random.shuffle(deck)
    return deck
