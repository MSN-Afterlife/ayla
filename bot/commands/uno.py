import random
import re
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.uno_images import UnoImageBuilder


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
CHAOS_VALUES = {"?", "?+8", "?+99", "?mao", "?troca", "?personagem", "\u2194"}
CHAOS_DECK_VALUES = ["?", "?", "?+8", "?+8", "?+99", "?mao", "?troca", "?personagem", "\u2194"]
CHAOS_DECK_SIZE = 500
CLASSIC_DECK_SIZE = 108
MAX_CHAOS_DECK_SIZE = 1_000_000
CHAOS_CHARACTERS = {
    "godzilla": {"name": "Godzilla", "effects": ["rampage", "roar", "stomp", "atomic", "tailwind"]},
    "kraken": {"name": "Kraken", "effects": ["tentacles", "drown", "gift", "tide", "steal"]},
    "dragao": {"name": "Dragao", "effects": ["fire", "hoard", "flight", "scales", "burn"]},
    "mothra": {"name": "Mothra", "effects": ["dust", "blessing", "flutter", "heal", "swarm"]},
    "minotauro": {"name": "Minotauro", "effects": ["charge", "labyrinth", "axe", "rage", "guard"]},
    "medusa": {"name": "Medusa", "effects": ["petrify", "gaze", "snakes", "curse", "mirror"]},
    "yeti": {"name": "Yeti", "effects": ["blizzard", "snowball", "warmth", "freeze", "avalanche"]},
    "fenix": {"name": "Fenix", "effects": ["rebirth", "flames", "ashes", "sun", "spark"]},
    "cthulhu": {"name": "Cthulhu", "effects": ["madness", "whispers", "void", "dream", "tentacles"]},
    "king_kong": {"name": "King Kong", "effects": ["smash", "roar", "climb", "protect", "throw"]},
    "slime": {"name": "Slime Mutante", "effects": ["split", "absorb", "bounce", "melt", "clone"]},
    "robo_caos": {"name": "Robo Caos", "effects": ["hack", "laser", "repair", "overload", "shuffle"]},
    "ayla_caotica": {"name": "Ayla Caotica", "effects": ["roulette", "favor", "prank", "glitch", "gift"]},
}
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

    @property
    def is_chaos(self) -> bool:
        return self.value in CHAOS_VALUES

    def label(self) -> str:
        if self.is_wild or self.is_chaos:
            return self.value.upper()
        return f"{self.color} {self.value}"


@dataclass
class UnoPlayer:
    member: discord.Member | discord.User
    hand: list[UnoCard] = field(default_factory=list)
    said_uno: bool = False


class UnoGame:
    def __init__(
        self,
        channel_id: int,
        host: discord.Member | discord.User,
        ruleset: str = "normal",
        ayla_caotica_url: str | None = None,
        chaos_deck_size: int = CHAOS_DECK_SIZE,
    ) -> None:
        self.channel_id = channel_id
        self.host_id = host.id
        self.ruleset = ruleset
        self.ayla_caotica_url = ayla_caotica_url
        self.chaos_deck_size = max(CLASSIC_DECK_SIZE, chaos_deck_size)
        self.players = [UnoPlayer(host)]
        self.deck = _new_uno_deck(chaos=self.is_chaos, chaos_deck_size=self.chaos_deck_size)
        self.discard: list[UnoCard] = []
        self.current_color: str | None = None
        self.turn_index = 0
        self.direction = 1
        self.started = False
        self.awaiting_draw = False
        self.pending_uno_player_id: int | None = None
        self.last_character: str | None = None
        self.last_character_url: str | None = None
        self.last_character_effect: str | None = None
        self.last_character_target: str | None = None

    @property
    def is_chaos(self) -> bool:
        return self.ruleset == "caos"

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

        # Escolhe uma carta inicial valida sem reembaralhar o baralho inteiro.
        # Isso e essencial para baralhos grandes, especialmente com 1 milhao de cartas.
        top_index = next(
            (
                index
                for index, card in enumerate(self.deck)
                if not card.is_wild and not card.is_chaos and card.value not in ACTION_VALUES
            ),
            None,
        )
        if top_index is None:
            raise RuntimeError("O baralho nao possui uma carta inicial valida.")
        top = self.deck.pop(top_index)

        self.discard.append(top)
        self.current_color = top.color
        self.started = True

    def draw_one(self) -> UnoCard | None:
        if not self.deck:
            if self.is_chaos:
                return None
            if len(self.discard) > 1:
                top = self.discard.pop()
                self.deck = self.discard
                self.discard = [top]
                random.shuffle(self.deck)
            else:
                self.deck = _new_uno_deck()
        return self.deck.pop()

    def can_play(self, card: UnoCard) -> bool:
        top = self.top_card
        return card.is_wild or card.is_chaos or card.color == self.current_color or card.value == top.value

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
        if self.is_chaos:
            self.deck.append(card)
        self.current_color = color if card.is_wild else (card.color or self.current_color)
        self.awaiting_draw = False
        self.last_character = None
        self.last_character_url = None
        self.last_character_effect = None
        self.last_character_target = None
        return True, self.apply_card_effect(card), card

    def apply_card_effect(self, card: UnoCard) -> str:
        if card.is_chaos:
            return self.apply_chaos_effect(card)
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
            self._draw_many(target, 2)
            self.advance(2)
            return f"{target.member.display_name} comprou 2 cartas e perdeu a vez."
        if card.value == "+4":
            target = self.next_player()
            self._draw_many(target, 4)
            self.advance(2)
            return f"{target.member.display_name} comprou 4 cartas e perdeu a vez."

        self.advance()
        return "Carta jogada."

    def apply_chaos_effect(self, card: UnoCard) -> str:
        if card.value == "?+8":
            target = self.next_player()
            drawn = self._draw_many(target, 8)
            self.advance(2)
            return f"{target.member.display_name} caiu no caos e comprou +{drawn} cartas."
        if card.value == "?+99":
            target = self.next_player()
            drawn = self._draw_many(target, 99)
            self.advance(2)
            if drawn == 0:
                return f"{target.member.display_name} escapou do +99: o baralho Caos estava vazio."
            return f"{target.member.display_name} foi esmagado pelo caos e comprou +{drawn} cartas."
        if card.value == "?mao":
            changed = max(1, len(self.current_player.hand) * 2 // 3)
            for index in random.sample(range(len(self.current_player.hand)), min(changed, len(self.current_player.hand))):
                self.current_player.hand[index] = UnoCard(None, random.choice(["+4", "?+8", "?+99"]))
            self.advance()
            return f"A mao de {self.current_player.member.display_name} virou uma mao coringa: {changed} carta(s) foram corrompidas."
        if card.value == "?troca":
            actor = self.current_player
            target = self._random_other_player()
            if target:
                actor.hand, target.hand = target.hand, actor.hand
                self.advance()
                return f"Troca maluca: {actor.member.display_name} trocou de mao com {target.member.display_name}."
        if card.value == "\u2194":
            actor = self.current_player
            target = self._player_with_fewest_cards()
            if target and actor.hand and target.hand:
                actor_index = random.randrange(len(actor.hand))
                target_index = random.randrange(len(target.hand))
                actor.hand[actor_index], target.hand[target_index] = target.hand[target_index], actor.hand[actor_index]
                self.advance()
                return f"Seta da troca: {actor.member.display_name} trocou uma carta aleatoria com {target.member.display_name}, que tinha menos cartas."
            self.advance()
            return "Seta da troca: nao havia duas maos com cartas para trocar."
        if card.value == "?personagem":
            message, steps = self._character_event_v2()
            self.advance(steps)
            return message

        event = random.choice(["compra", "troca", "mao", "personagem", "cor"])
        if event == "compra":
            target = self._random_other_player()
            amount = random.choice([2, 4, 8, 13])
            if target:
                self._draw_many(target, amount)
                self.advance(2)
                return f"Evento aleatorio: {target.member.display_name} comprou +{amount} cartas."
        elif event == "troca":
            actor = self.current_player
            target = self._random_other_player()
            if target:
                actor.hand, target.hand = target.hand, actor.hand
                self.advance()
                return f"Evento aleatorio: {actor.member.display_name} trocou de mao com {target.member.display_name}."
        elif event == "mao":
            amount = max(1, len(self.current_player.hand) // 2)
            for index in random.sample(range(len(self.current_player.hand)), min(amount, len(self.current_player.hand))):
                self.current_player.hand[index] = UnoCard(None, random.choice(["+4", "?+8", "?+99"]))
            self.advance()
            return f"Evento aleatorio: {amount} carta(s) da mao foram transformadas em cartas especiais."
        elif event == "personagem":
            message, steps = self._character_event_v2()
            self.advance(steps)
            return message
        else:
            self.current_color = random.choice(list(COLORS))
            self.advance()
            return f"Evento aleatorio: a cor do caos agora e {self.current_color}."

        self.advance()
        return "O caos tentou agir, mas tropeçou no proprio baralho."

    def _draw_many(self, player: UnoPlayer, amount: int) -> int:
        cards = [self.draw_one() for _ in range(amount)]
        cards = [card for card in cards if card is not None]
        player.hand.extend(cards)
        return len(cards)

    def _random_other_player(self) -> UnoPlayer | None:
        others = [player for player in self.players if player is not self.current_player]
        return random.choice(others) if others else None

    def _player_with_fewest_cards(self) -> UnoPlayer | None:
        others = [player for player in self.players if player is not self.current_player]
        if not others:
            return None
        smallest = min(len(player.hand) for player in others)
        tied = [player for player in others if len(player.hand) == smallest]
        return random.choice(tied)

    def _character_event(self) -> str:
        character = random.choice(["Godzilla", "A Bruxa do Baralho", "O Ladrao de Cartas", "Ayla Caotica"])
        target = self._random_other_player()
        if not target:
            return f"{character} apareceu, mas nao encontrou ninguem para atormentar."
        if character == "Godzilla":
            self._draw_many(target, 50)
            return f"Godzilla apareceu e atacou {target.member.display_name}: +50 cartas!"
        if character == "A Bruxa do Baralho":
            self._draw_many(target, 6)
            return f"A Bruxa do Baralho amaldiçoou {target.member.display_name}: +6 cartas!"
        if character == "O Ladrao de Cartas":
            if target.hand:
                self.current_player.hand.append(target.hand.pop(random.randrange(len(target.hand))))
            return f"O Ladrao de Cartas roubou uma carta de {target.member.display_name}."
        self.current_player.hand, target.hand = target.hand, self.current_player.hand
        return f"Ayla Caotica trocou as maos de {self.current_player.member.display_name} e {target.member.display_name}."

    def _character_event_v2(self) -> tuple[str, int]:
        key = random.choice(list(CHAOS_CHARACTERS))
        character = CHAOS_CHARACTERS[key]
        self.last_character = key
        self.last_character_url = self.ayla_caotica_url if key == "ayla_caotica" else None
        effect = random.choice(character["effects"])
        target = self._random_other_player()
        self.last_character_effect = effect
        self.last_character_target = target.member.display_name if target else None
        if not target:
            return f"{character['name']} apareceu, mas nao encontrou ninguem para atormentar.", 1
        return self._apply_character_effect(character["name"], effect, target)

    def _apply_character_effect(self, character: str, effect: str, target: UnoPlayer) -> tuple[str, int]:
        actor = self.current_player.member.display_name
        victim = target.member.display_name
        if effect in {"rampage", "tentacles", "fire", "smash"}:
            amount = {"rampage": 50, "tentacles": 12, "fire": 10, "smash": 15}[effect]
            self._draw_many(target, amount)
            return f"{character} usou {effect}: {victim} comprou +{amount} cartas e perdeu a vez.", 2
        if effect in {"roar", "drown", "burn", "charge", "avalanche", "overload"}:
            amount = {"roar": 5, "drown": 7, "burn": 6, "charge": 9, "avalanche": 12, "overload": 20}[effect]
            self._draw_many(target, amount)
            return f"{character} atacou {victim} com {effect}: +{amount} cartas.", 1
        if effect in {"stomp", "petrify", "freeze", "hack"}:
            self.current_color = random.choice(list(COLORS))
            return f"{character} alterou a cor para {self.current_color} e travou a jogada de {victim}.", 2
        if effect in {"atomic", "laser", "gaze", "void", "axe"}:
            self._transform_hand(target, max(2, len(target.hand) // 2))
            return f"{character} deformou a mao de {victim} em cartas de caos.", 1
        if effect in {"tailwind", "flight", "flutter", "climb", "bounce", "spark", "sun"}:
            self.current_player.hand.extend(self._draw_many_list(3))
            return f"{character} beneficiou {actor}: voce ganhou 3 cartas extras para preparar a proxima jogada.", 0
        if effect in {"gift", "blessing", "heal", "protect", "repair", "favor"}:
            self._remove_random_cards(self.current_player, min(3, len(self.current_player.hand)))
            return f"{character} protegeu {actor} e removeu ate 3 cartas ruins da sua mao.", 1
        if effect in {"tide", "blizzard"}:
            self.current_color = random.choice(list(COLORS))
            self._draw_many(target, 3)
            return f"{character} mudou a cor para {self.current_color} e atingiu {victim} com +3 cartas.", 1
        if effect in {"hoard", "snowball", "flames"}:
            amount = {"hoard": 5, "snowball": 2, "flames": 7}[effect]
            self._draw_many(target, amount)
            self.current_player.hand.extend(self._draw_many_list(2))
            return f"{character} criou {effect}: {victim} compra +{amount} e {actor} ganha 2 cartas.", 1
        if effect in {"scales", "guard", "rage", "rebirth", "ashes", "dream", "clone", "split"}:
            self._remove_random_cards(self.current_player, min(2, len(self.current_player.hand)))
            self.current_player.hand.extend(self._draw_many_list(4))
            return f"{character} ativou {effect}: {actor} perdeu cartas ruins e recebeu 4 novas.", 1
        if effect in {"melt", "absorb"}:
            self._transform_hand(target, min(5, len(target.hand)))
            self._draw_many(target, 2)
            return f"{character} derreteu a mao de {victim} e deixou 2 cartas de caos no lugar.", 1
        if effect in {"steal", "hoard", "absorb", "throw", "prank"}:
            if target.hand:
                self.current_player.hand.append(target.hand.pop(random.randrange(len(target.hand))))
            self._draw_many(target, 3)
            return f"{character} roubou uma carta de {victim} e deixou +3 cartas no lugar.", 1
        if effect in {"dust", "swarm", "snakes", "whispers", "madness", "curse", "melt"}:
            self._draw_many(target, 4)
            self.direction *= -1
            return f"{character} espalhou {effect}: {victim} compra 4 e a ordem foi invertida.", 1
        if effect in {"labyrinth", "mirror", "shuffle", "glitch", "roulette"}:
            random.shuffle(target.hand)
            self.current_color = random.choice(list(COLORS))
            return f"{character} embaralhou a mao de {victim} e mudou a cor para {self.current_color}.", 1
        self._transform_hand(self.current_player, min(4, len(self.current_player.hand)))
        self.current_player.hand.extend(self._draw_many_list(2))
        return f"{character} fortaleceu {actor}: ganhou 2 cartas e recebeu cartas especiais.", 1

    def _draw_many_list(self, amount: int) -> list[UnoCard]:
        return [card for card in (self.draw_one() for _ in range(amount)) if card is not None]

    def _transform_hand(self, player: UnoPlayer, amount: int) -> None:
        if not player.hand:
            return
        indices = random.sample(range(len(player.hand)), min(amount, len(player.hand)))
        for index in indices:
            player.hand[index] = UnoCard(None, random.choice(["+4", "?+8", "?+99"]))

    def _remove_random_cards(self, player: UnoPlayer, amount: int) -> None:
        for _ in range(min(amount, len(player.hand))):
            player.hand.pop(random.randrange(len(player.hand)))

    def next_player(self) -> UnoPlayer:
        return self.players[(self.turn_index + self.direction) % len(self.players)]

    def advance(self, steps: int = 1) -> None:
        self.turn_index = (self.turn_index + self.direction * steps) % len(self.players)
        self.current_player.said_uno = False

    def draw_for_current(self) -> UnoCard | None:
        card = self.draw_one()
        if card is not None:
            self.current_player.hand.append(card)
        self.awaiting_draw = True
        return card

    def pass_turn(self) -> None:
        self.awaiting_draw = False
        self.advance()

    def winner(self) -> UnoPlayer | None:
        return next((player for player in self.players if not player.hand), None)


class UnoRulesView(discord.ui.View):
    def __init__(self, game: UnoGame) -> None:
        super().__init__(timeout=300)
        self.game = game
        self.source_message: discord.Message | None = None

    async def _set_rules(self, interaction: discord.Interaction, ruleset: str) -> None:
        if interaction.user.id != self.game.host_id:
            await interaction.response.send_message("So quem criou a mesa pode escolher as regras.", ephemeral=True)
            return
        if self.game.started:
            await interaction.response.send_message("A partida ja foi iniciada.", ephemeral=True)
            return
        self.game.ruleset = ruleset
        self.game.deck = _new_uno_deck(chaos=self.game.is_chaos, chaos_deck_size=self.game.chaos_deck_size)
        self.source_message = interaction.message
        try:
            # Confirma o clique dentro da janela do Discord e depois edita a
            # mensagem pelo endpoint normal, evitando tokens de interacao expirados.
            await interaction.response.defer()
            await interaction.message.edit(content=_rules_prompt(self.game), view=self)
        except discord.NotFound:
            # O painel pode ter sido apagado ou o clique pode ter chegado tarde.
            # Nesse caso a partida continua intacta e nao ha traceback para poluir o log.
            return

    @discord.ui.button(label="Entrar na mesa", style=discord.ButtonStyle.success, row=1)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.source_message = interaction.message
        if self.game.started:
            await interaction.response.send_message("A partida ja foi iniciada.", ephemeral=True)
            return
        if self.game.add_player(interaction.user):
            await interaction.response.send_message(
                f"{interaction.user.mention} entrou na mesa. Jogadores: `{len(self.game.players)}`."
            )
            if self.source_message:
                await self.source_message.edit(content=_rules_prompt(self.game), view=self)
            return
        await interaction.response.send_message("Voce ja esta na mesa.", ephemeral=True)

    @discord.ui.button(label="Tamanho do baralho", style=discord.ButtonStyle.secondary, row=1)
    async def deck_size(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.source_message = interaction.message
        if interaction.user.id != self.game.host_id:
            await interaction.response.send_message("So quem criou a mesa pode alterar o tamanho do baralho.", ephemeral=True)
            return
        if self.game.started:
            await interaction.response.send_message("A partida ja foi iniciada.", ephemeral=True)
            return
        await interaction.response.send_modal(UnoDeckSizeModal(self.game, self))

    @discord.ui.button(label="UNO normal", style=discord.ButtonStyle.primary)
    async def normal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_rules(interaction, "normal")

    @discord.ui.button(label="UNO Caos", style=discord.ButtonStyle.danger)
    async def chaos(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_rules(interaction, "caos")


class UnoDeckSizeModal(discord.ui.Modal, title="Tamanho do baralho Caos"):
    size = discord.ui.TextInput(
        label="Quantidade total de cartas",
        placeholder=f"Digite um valor entre {CLASSIC_DECK_SIZE} e {MAX_CHAOS_DECK_SIZE}",
        min_length=3,
        max_length=7,
        required=True,
    )

    def __init__(self, game: UnoGame, view: UnoRulesView) -> None:
        super().__init__()
        self.game = game
        self.rules_view = view
        self.size.default = str(game.chaos_deck_size)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.game.host_id:
            await interaction.response.send_message("So quem criou a mesa pode alterar o tamanho do baralho.", ephemeral=True)
            return
        try:
            size = int(str(self.size.value).strip())
        except ValueError:
            await interaction.response.send_message("Digite apenas um numero inteiro.", ephemeral=True)
            return
        if not CLASSIC_DECK_SIZE <= size <= MAX_CHAOS_DECK_SIZE:
            await interaction.response.send_message(
                f"O tamanho deve ficar entre `{CLASSIC_DECK_SIZE}` e `{MAX_CHAOS_DECK_SIZE}` cartas.",
                ephemeral=True,
            )
            return

        self.game.chaos_deck_size = size
        if self.game.is_chaos:
            self.game.deck = _new_uno_deck(chaos=True, chaos_deck_size=size)
        await interaction.response.send_message(f"Baralho Caos definido para `{size}` cartas.", ephemeral=True)
        if self.rules_view.source_message:
            await self.rules_view.source_message.edit(content=_rules_prompt(self.game), view=self.rules_view)


def setup_uno_commands(bot: commands.Bot, settings: Settings | None = None) -> None:
    games: dict[int, UnoGame] = {}
    images = UnoImageBuilder()
    ayla_caotica_url = settings.ayla_caotica_url if settings else None
    chaos_deck_size = settings.uno_chaos_deck_size if settings else CHAOS_DECK_SIZE
    uno_slash = app_commands.Group(name="uno", description="Joga UNO da Ayla com 2+ jogadores.")

    @bot.group(name="uno", invoke_without_command=True)
    async def uno(ctx: commands.Context) -> None:
        await ctx.send(
            "UNO: `a!uno criar`, `entrar`, `iniciar`, `mao`, `jogar <numero> [cor]`, "
            "`comprar`, `passar`, `mesa`, `cancelar`."
        )

    @uno.command(name="criar")
    async def uno_create(ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("UNO precisa ser criado dentro de um servidor.")
            return
        if ctx.channel.id in games:
            await ctx.send("Ja existe uma mesa de UNO neste canal.")
            return

        games[ctx.channel.id] = UnoGame(ctx.channel.id, ctx.author, ayla_caotica_url=ayla_caotica_url, chaos_deck_size=chaos_deck_size)
        await ctx.send(_rules_prompt(games[ctx.channel.id]), view=UnoRulesView(games[ctx.channel.id]))

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
        setattr(game, "_images", images)
        failed = await _dm_all_hands(game)
        message = "Nao consegui mandar DM para: " + ", ".join(failed) if failed else None
        await ctx.send(content=message, embed=_table_embed(game, f"Partida iniciada no modo {game.ruleset.upper()}."), file=await images.build_table_image(game.top_card, current_color=game.current_color))

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
        await ctx.send(embed=_table_embed(game), file=await images.build_table_image(game.top_card, current_color=game.current_color))

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

        if len(acting_player.hand) == 1:
            game.pending_uno_player_id = acting_player.member.id
            message += f" {acting_player.member.mention} ficou com uma carta! Escreva `uno` agora. Se alguem escrever `compra` antes, compra 2 cartas."

        await _send_hand(game.current_player, game)
        await ctx.send(
            embed=_table_embed(game, message),
            file=await images.build_table_image(
                game.top_card,
                current_color=game.current_color,
                character=game.last_character if game.is_chaos else None,
                character_url=game.last_character_url if game.is_chaos else None,
                character_effect=game.last_character_effect if game.is_chaos else None,
                character_target=game.last_character_target if game.is_chaos else None,
            ),
        )

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
        if card is None:
            await ctx.send(f"{ctx.author.mention} tentou comprar, mas o baralho Caos esta vazio. Use `a!uno passar`.")
        else:
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
        await ctx.send(embed=_table_embed(game, "Vez passada."), file=await images.build_table_image(game.top_card, current_color=game.current_color))

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

        game = UnoGame(interaction.channel.id, interaction.user, ayla_caotica_url=ayla_caotica_url, chaos_deck_size=chaos_deck_size)
        games[interaction.channel.id] = game
        await interaction.response.send_message(_rules_prompt(game), view=UnoRulesView(game))

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
        setattr(game, "_images", images)
        failed = await _dm_all_hands(game)
        message = "Nao consegui mandar DM para: " + ", ".join(failed) if failed else None
        await interaction.response.send_message(content=message, embed=_table_embed(game, f"Partida iniciada no modo {game.ruleset.upper()}."), file=await images.build_table_image(game.top_card, current_color=game.current_color))

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
        await interaction.response.send_message(embed=_table_embed(game), file=await images.build_table_image(game.top_card, current_color=game.current_color))

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

        if len(acting_player.hand) == 1:
            game.pending_uno_player_id = acting_player.member.id
            message += f" {acting_player.member.mention} ficou com uma carta! Escreva `uno` agora. Se alguem escrever `compra` antes, compra 2 cartas."

        await _send_hand(game.current_player, game)
        await interaction.response.send_message(
            embed=_table_embed(game, message),
            file=await images.build_table_image(
                game.top_card,
                current_color=game.current_color,
                character=game.last_character if game.is_chaos else None,
                character_url=game.last_character_url if game.is_chaos else None,
                character_effect=game.last_character_effect if game.is_chaos else None,
                character_target=game.last_character_target if game.is_chaos else None,
            ),
        )

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
        card = game.draw_for_current()
        await _send_hand(game.current_player, game)
        if card is None:
            await interaction.response.send_message(f"{interaction.user.mention} tentou comprar, mas o baralho Caos esta vazio. Use `/uno passar`.")
        else:
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
        await interaction.response.send_message(embed=_table_embed(game, "Vez passada."), file=await images.build_table_image(game.top_card, current_color=game.current_color))

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

    async def handle_uno_reaction(message: discord.Message) -> bool:
        """Processa UNO, denuncia e jogadas digitadas no canal ou no privado."""
        content = message.content.casefold().strip().strip("!?.,")
        game = games.get(message.channel.id) if message.guild else next(
            (candidate for candidate in games.values() if candidate.started and _find_player(candidate, message.author.id)),
            None,
        )
        if not game or not game.started:
            return False

        player = _find_player(game, message.author.id)
        if not player:
            return False
        output_channel = message.channel if message.guild else bot.get_channel(game.channel_id)
        if output_channel is None:
            output_channel = message.channel

        if content == "uno":
            if len(player.hand) != 1:
                return False
            player.said_uno = True
            if game.pending_uno_player_id == player.member.id:
                game.pending_uno_player_id = None
            await output_channel.send(f"{player.member.mention} falou UNO a tempo!")
            return True

        if content in {"compra", "comprar"}:
            if game.pending_uno_player_id is None or message.author.id == game.pending_uno_player_id:
                return False
            target = _find_player(game, game.pending_uno_player_id)
            if not target or len(target.hand) != 1 or target.said_uno:
                game.pending_uno_player_id = None
                return False
            game._draw_many(target, 2)
            game.pending_uno_player_id = None
            await _send_hand(target, game)
            await output_channel.send(
                f"{message.author.mention} denunciou primeiro! {target.member.mention} comprou 2 cartas por nao falar UNO."
            )
            return True

        match = re.fullmatch(r"(\d+)(?:\s+(.+))?", content)
        if not match or player.member.id != game.current_player.member.id:
            return False

        card_number = int(match.group(1))
        color = _normalize_color(match.group(2)) if match.group(2) else None
        acting_player = game.current_player
        ok, result, card = game.play(card_number - 1, color)
        if not ok:
            await message.channel.send(result)
            return True

        await _send_hand(acting_player, game)
        winner = game.winner()
        if winner:
            games.pop(game.channel_id, None)
            await output_channel.send(embed=_winner_embed(winner, card))
            return True

        if len(acting_player.hand) == 1:
            game.pending_uno_player_id = acting_player.member.id
            result += f" {acting_player.member.mention} ficou com uma carta! Escreva `uno` agora. Se alguem escrever `compra` antes, compra 2 cartas."

        await _send_hand(game.current_player, game)
        await output_channel.send(
            embed=_table_embed(game, result),
            file=await images.build_table_image(
                game.top_card,
                current_color=game.current_color,
                character=game.last_character if game.is_chaos else None,
                character_url=game.last_character_url if game.is_chaos else None,
                character_effect=game.last_character_effect if game.is_chaos else None,
                character_target=game.last_character_target if game.is_chaos else None,
            ),
        )
        return True

    bot._handle_uno_reaction = handle_uno_reaction


async def _dm_all_hands(game: UnoGame) -> list[str]:
    failed = []
    for player in game.players:
        if not await _send_hand(player, game):
            failed.append(player.member.display_name)
    return failed


async def _send_hand(player: UnoPlayer, game: UnoGame) -> bool:
    try:
        builder = getattr(game, "_images")
        await player.member.send(
            content=(
                f"Topo: {_top_label(game)} | Vez: {game.current_player.member.display_name}\n"
                f"Modo: {game.ruleset.upper()}.\n"
                "Como jogar: a imagem mostra o numero de cada carta. Envie somente o numero da carta no chat da mesa ou aqui neste privado para jogar. "
                "Para coringa ou +4, envie numero e cor (por exemplo: `3 azul`). Se ficar com uma carta, escreva `uno` no chat. "
                "Se alguem escrever `compra` antes, voce compra 2 cartas. Para comprar, use `a!uno comprar` no chat da mesa."
            ),
            file=await builder.build_hand_image(player.hand, current_color=game.current_color, top_card=game.top_card),
        )
        return True
    except discord.HTTPException:
        return False
    except AttributeError:
        return False


def _table_embed(game: UnoGame, note: str | None = None) -> discord.Embed:
    embed = discord.Embed(title="UNO da Ayla", description=note, color=COLORS.get(game.current_color or "azul", 0x7AA7FF))
    embed.add_field(name="Topo", value=_top_label(game), inline=True)
    embed.add_field(name="Cor atual", value=game.current_color or "nenhuma", inline=True)
    embed.add_field(name="Regras", value=game.ruleset.upper(), inline=True)
    if game.is_chaos:
        embed.add_field(name="Estoque Caos", value=f"`{len(game.deck)}/{game.chaos_deck_size}`", inline=True)
    if game.last_character:
        character_name = CHAOS_CHARACTERS[game.last_character]["name"]
        embed.add_field(name="Personagem", value=character_name, inline=True)
        if game.last_character_effect:
            embed.add_field(name="Acao visual", value=game.last_character_effect.upper(), inline=True)
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


def _rules_prompt(game: UnoGame) -> str:
    if game.is_chaos:
        description = (
            "Cartas `?` ativam eventos aleatorios. O baralho inclui ataques de +8 e +99, "
            f"trocas de mao, carta ↔ contra quem tem menos cartas, transformacoes especiais e {game.chaos_deck_size} cartas de estoque. "
            "Existem 13 personagens, cada um com 5 efeitos diferentes."
        )
    else:
        description = "Somente as regras classicas do UNO, sem eventos extras."
    return (
        f"Mesa de UNO criada por <@{game.host_id}>. Jogadores: `{len(game.players)}`.\n"
        f"Modo selecionado: **{game.ruleset.upper()}**. {description}\n"
        "O criador pode trocar o modo nos botoes abaixo antes de usar `a!uno iniciar`."
    )


def _normalize_color(color: str | None) -> str | None:
    if not color:
        return None
    normalized = color.lower()
    return COLOR_ALIASES.get(normalized, normalized)


def _new_uno_deck(*, chaos: bool = False, chaos_deck_size: int = CHAOS_DECK_SIZE) -> list[UnoCard]:
    classic_cards: list[UnoCard] = []
    for color in COLORS:
        classic_cards.append(UnoCard(color, "0"))
        for value in VALUES[1:]:
            classic_cards.append(UnoCard(color, value))
            classic_cards.append(UnoCard(color, value))
    for _ in range(4):
        classic_cards.append(UnoCard(None, "coringa"))
        classic_cards.append(UnoCard(None, "+4"))

    if not chaos:
        random.shuffle(classic_cards)
        return classic_cards

    size = max(CLASSIC_DECK_SIZE, chaos_deck_size)
    chaos_count = max(5, round(size * 0.30))
    normal_count = size - chaos_count
    deck = random.choices(classic_cards, k=normal_count)

    chaos_cards = [UnoCard(None, value) for value in CHAOS_DECK_VALUES]
    # Garante pelo menos cinco cartas de +99, mesmo em um baralho pequeno.
    deck.extend(UnoCard(None, "?+99") for _ in range(5))
    deck.extend(random.choices(chaos_cards, k=chaos_count - 5))
    random.shuffle(deck)
    return deck
