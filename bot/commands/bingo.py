from __future__ import annotations

import io
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont


COLORS = {"B": (67, 132, 235), "I": (224, 92, 92), "N": (55, 176, 112), "G": (235, 170, 58), "O": (158, 91, 205)}
RANGES = {"B": range(1, 16), "I": range(16, 31), "N": range(31, 46), "G": range(46, 61), "O": range(61, 76)}


@dataclass
class BingoPlayer:
    member: discord.Member | discord.User
    card: list[list[int | None]] = field(default_factory=list)
    marked: set[int] = field(default_factory=set)

    def has_bingo(self) -> bool:
        rows = [set(row) - {None} for row in self.card]
        columns = [
            {self.card[row][column] for row in range(5)} - {None}
            for column in range(5)
        ]
        diagonals = [
            {self.card[index][index] for index in range(5)} - {None},
            {self.card[index][4 - index] for index in range(5)} - {None},
        ]
        return any(line <= self.marked for line in (*rows, *columns, *diagonals))


class BingoGame:
    def __init__(self, channel_id: int, host: discord.Member | discord.User) -> None:
        self.channel_id = channel_id
        self.host_id = host.id
        self.players: list[BingoPlayer] = [BingoPlayer(host)]
        self.called: list[int] = []
        self.started = False
        self.finished = False

    def add_player(self, member: discord.Member | discord.User) -> bool:
        if self.started or self.finished or any(player.member.id == member.id for player in self.players):
            return False
        self.players.append(BingoPlayer(member))
        return True

    def start(self) -> None:
        for player in self.players:
            player.card = _new_card()
            player.marked = {player.card[2][2]}  # casa livre
        self.called.clear()
        self.started = True
        self.finished = False

    def draw(self) -> int | None:
        if not self.started or self.finished:
            return None
        available = [number for number in range(1, 76) if number not in self.called]
        if not available:
            return None
        number = random.choice(available)
        self.called.append(number)
        return number

    def mark(self, user_id: int, number: int) -> tuple[bool, str]:
        player = _find_player(self, user_id)
        if not player:
            return False, "Voce nao esta nessa partida."
        if number not in self.called:
            return False, "Esse numero ainda nao foi sorteado."
        if number not in {value for row in player.card for value in row if value is not None}:
            return False, "Esse numero nao esta na sua cartela."
        if number in player.marked:
            return False, "Esse numero ja esta marcado."
        player.marked.add(number)
        return True, f"Numero `{number}` marcado."

    def claim(self, user_id: int) -> tuple[bool, str, BingoPlayer | None]:
        player = _find_player(self, user_id)
        if not player:
            return False, "Voce nao esta nessa partida.", None
        if not player.has_bingo():
            return False, "Ainda nao ha uma linha completa na sua cartela.", None
        self.finished = True
        return True, f"{player.member.mention} fez BINGO!", player


def setup_bingo_commands(bot: commands.Bot) -> None:
    games: dict[int, BingoGame] = {}
    bingo_slash = app_commands.Group(name="bingo", description="Joga Bingo da Ayla com cartelas privadas.")

    @bot.group(name="bingo", invoke_without_command=True)
    async def bingo(ctx: commands.Context) -> None:
        await ctx.send("Bingo: `a!bingo criar`, `entrar`, `iniciar`, `sortear`, `marcar <numero>`, `bingo`, `cartela`, `mesa`, `cancelar`.")

    @bingo.command(name="criar")
    async def create(ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("Bingo precisa ser criado dentro de um servidor.")
            return
        if ctx.channel.id in games:
            await ctx.send("Ja existe uma partida de Bingo neste canal.")
            return
        games[ctx.channel.id] = BingoGame(ctx.channel.id, ctx.author)
        await ctx.send(f"Mesa de Bingo criada por {ctx.author.mention}. Use `a!bingo entrar` para participar.")

    @bingo.command(name="entrar")
    async def join(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game:
            await ctx.send("Nao tem partida de Bingo neste canal. Use `a!bingo criar`.")
            return
        if game.add_player(ctx.author):
            await ctx.send(f"{ctx.author.mention} entrou no Bingo. Jogadores: `{len(game.players)}`.")
        else:
            await ctx.send("Voce ja esta na partida ou ela ja comecou.")

    @bingo.command(name="iniciar")
    async def start(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game:
            await ctx.send("Nao tem partida de Bingo neste canal.")
            return
        if ctx.author.id != game.host_id:
            await ctx.send("Apenas quem criou a mesa pode iniciar.")
            return
        if len(game.players) < 2:
            await ctx.send("Bingo precisa de pelo menos 2 jogadores.")
            return
        game.start()
        failed = await _dm_all_cards(game)
        note = "Partida iniciada! As cartelas foram enviadas no privado. Use `a!bingo sortear`."
        if failed:
            note += "\nNao consegui mandar DM para: " + ", ".join(failed)
        await ctx.send(content=note, file=_board_file(game), embed=_table_embed(game, "Primeira rodada pronta."))

    @bingo.command(name="sortear")
    async def draw(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started or game.finished:
            await ctx.send("Nao ha uma partida de Bingo ativa neste canal.")
            return
        if ctx.author.id != game.host_id and not getattr(ctx.author.guild_permissions, "manage_guild", False):
            await ctx.send("Apenas quem criou a mesa ou gerencia o servidor pode sortear.")
            return
        number = game.draw()
        if number is None:
            await ctx.send("Nao ha mais numeros para sortear.")
            return
        await ctx.send(file=_board_file(game), embed=_table_embed(game, f"Numero sorteado: **{number}**"))

    @bingo.command(name="marcar")
    async def mark(ctx: commands.Context, numero: int) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started or game.finished:
            await ctx.send("Nao ha uma partida de Bingo ativa neste canal.")
            return
        if numero < 1 or numero > 75:
            await ctx.send("Escolha um numero entre 1 e 75.")
            return
        ok, message = game.mark(ctx.author.id, numero)
        if not ok:
            await ctx.send(message)
            return
        player = _find_player(game, ctx.author.id)
        await _send_card(player, game)
        await ctx.send(f"{ctx.author.mention} marcou o numero `{numero}` no privado.")

    @bingo.command(name="bingo")
    async def claim(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started or game.finished:
            await ctx.send("Nao ha uma partida de Bingo ativa neste canal.")
            return
        ok, message, player = game.claim(ctx.author.id)
        if not ok:
            await ctx.send(message)
            return
        games.pop(ctx.channel.id, None)
        await ctx.send(content=message, file=_card_file(player, game, winner=True), embed=_winner_embed(player))

    @bingo.command(name="cartela")
    async def card(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        player = _find_player(game, ctx.author.id) if game else None
        if not game or not game.started or not player:
            await ctx.send("Voce nao esta em uma partida de Bingo iniciada.")
            return
        if await _send_card(player, game):
            await ctx.send("Mandei sua cartela atualizada no privado.")
        else:
            await ctx.send("Nao consegui te mandar DM. Abra suas mensagens privadas para a Ayla.")

    @bingo.command(name="mesa")
    async def table(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game or not game.started:
            await ctx.send("Nao ha partida de Bingo iniciada neste canal.")
            return
        await ctx.send(file=_board_file(game), embed=_table_embed(game))

    @bingo.command(name="cancelar")
    async def cancel(ctx: commands.Context) -> None:
        game = games.get(ctx.channel.id)
        if not game:
            await ctx.send("Nao tem partida de Bingo neste canal.")
            return
        allowed = ctx.author.id == game.host_id or getattr(ctx.author.guild_permissions, "manage_guild", False)
        if not allowed:
            await ctx.send("Apenas quem criou a mesa ou gerencia o servidor pode cancelar.")
            return
        games.pop(ctx.channel.id, None)
        await ctx.send("Partida de Bingo cancelada.")

    @bingo_slash.command(name="criar", description="Cria uma mesa de Bingo neste canal.")
    async def create_slash(interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("Bingo precisa ser criado dentro de um servidor.", ephemeral=True)
            return
        if interaction.channel.id in games:
            await interaction.response.send_message("Ja existe uma partida de Bingo neste canal.", ephemeral=True)
            return
        games[interaction.channel.id] = BingoGame(interaction.channel.id, interaction.user)
        await interaction.response.send_message(f"Mesa de Bingo criada por {interaction.user.mention}. Use `/bingo entrar` para participar.")

    @bingo_slash.command(name="entrar", description="Entra na mesa de Bingo deste canal.")
    async def join_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game:
            await interaction.response.send_message("Nao tem partida de Bingo neste canal.", ephemeral=True)
            return
        if game.add_player(interaction.user):
            await interaction.response.send_message(f"{interaction.user.mention} entrou no Bingo. Jogadores: `{len(game.players)}`.")
        else:
            await interaction.response.send_message("Voce ja esta na partida ou ela ja comecou.", ephemeral=True)

    @bingo_slash.command(name="iniciar", description="Inicia o Bingo e envia as cartelas por DM.")
    async def start_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game:
            await interaction.response.send_message("Nao tem partida de Bingo neste canal.", ephemeral=True)
            return
        if interaction.user.id != game.host_id:
            await interaction.response.send_message("Apenas quem criou a mesa pode iniciar.", ephemeral=True)
            return
        if len(game.players) < 2:
            await interaction.response.send_message("Bingo precisa de pelo menos 2 jogadores.", ephemeral=True)
            return
        game.start()
        failed = await _dm_all_cards(game)
        note = "Partida iniciada! As cartelas foram enviadas no privado. Use `/bingo sortear`."
        if failed:
            note += " Nao consegui mandar DM para: " + ", ".join(failed)
        await interaction.response.send_message(content=note, file=_board_file(game), embed=_table_embed(game, "Primeira rodada pronta."))

    @bingo_slash.command(name="sortear", description="Sorteia o proximo numero do Bingo.")
    async def draw_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game or not game.started or game.finished:
            await interaction.response.send_message("Nao ha uma partida de Bingo ativa neste canal.", ephemeral=True)
            return
        permissions = getattr(interaction.user, "guild_permissions", None)
        if interaction.user.id != game.host_id and not bool(permissions and permissions.manage_guild):
            await interaction.response.send_message("Apenas o criador ou quem gerencia o servidor pode sortear.", ephemeral=True)
            return
        number = game.draw()
        if number is None:
            await interaction.response.send_message("Nao ha mais numeros para sortear.", ephemeral=True)
            return
        await interaction.response.send_message(file=_board_file(game), embed=_table_embed(game, f"Numero sorteado: **{number}**"))

    @bingo_slash.command(name="marcar", description="Marca na sua cartela um numero ja sorteado.")
    @app_commands.describe(numero="Numero entre 1 e 75 que ja foi sorteado.")
    async def mark_slash(interaction: discord.Interaction, numero: int) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game or not game.started or game.finished:
            await interaction.response.send_message("Nao ha uma partida de Bingo ativa neste canal.", ephemeral=True)
            return
        ok, message = game.mark(interaction.user.id, numero)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await _send_card(_find_player(game, interaction.user.id), game)
        await interaction.response.send_message(f"Numero `{numero}` marcado. Cartela atualizada no privado.", ephemeral=True)

    @bingo_slash.command(name="bingo", description="Declara Bingo e verifica sua cartela.")
    async def claim_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game or not game.started or game.finished:
            await interaction.response.send_message("Nao ha uma partida de Bingo ativa neste canal.", ephemeral=True)
            return
        ok, message, player = game.claim(interaction.user.id)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        games.pop(game.channel_id, None)
        await interaction.response.send_message(content=message, file=_card_file(player, game, winner=True), embed=_winner_embed(player))

    @bingo_slash.command(name="cartela", description="Reenvia sua cartela por DM.")
    async def card_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        player = _find_player(game, interaction.user.id) if game else None
        if not game or not game.started or not player:
            await interaction.response.send_message("Voce nao esta em uma partida de Bingo iniciada.", ephemeral=True)
            return
        if await _send_card(player, game):
            await interaction.response.send_message("Cartela enviada no privado.", ephemeral=True)
        else:
            await interaction.response.send_message("Nao consegui te mandar DM. Abra suas mensagens privadas para a Ayla.", ephemeral=True)

    @bingo_slash.command(name="mesa", description="Mostra o painel da mesa de Bingo.")
    async def table_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game or not game.started:
            await interaction.response.send_message("Nao ha partida de Bingo iniciada neste canal.", ephemeral=True)
            return
        await interaction.response.send_message(file=_board_file(game), embed=_table_embed(game))

    @bingo_slash.command(name="cancelar", description="Cancela a partida de Bingo.")
    async def cancel_slash(interaction: discord.Interaction) -> None:
        game = games.get(interaction.channel.id) if interaction.channel else None
        if not game:
            await interaction.response.send_message("Nao tem partida de Bingo neste canal.", ephemeral=True)
            return
        permissions = getattr(interaction.user, "guild_permissions", None)
        if interaction.user.id != game.host_id and not bool(permissions and permissions.manage_guild):
            await interaction.response.send_message("Apenas o criador ou quem gerencia o servidor pode cancelar.", ephemeral=True)
            return
        games.pop(game.channel_id, None)
        await interaction.response.send_message("Partida de Bingo cancelada.")

    bot.tree.add_command(bingo_slash)


async def _dm_all_cards(game: BingoGame) -> list[str]:
    failed = []
    for player in game.players:
        if not await _send_card(player, game):
            failed.append(player.member.display_name)
    return failed


async def _send_card(player: BingoPlayer | None, game: BingoGame) -> bool:
    if not player:
        return False
    try:
        await player.member.send(
            "Sua cartela do Bingo da Ayla. Marque no canal com `a!bingo marcar <numero>` "
            "ou `/bingo marcar`, e declare com `a!bingo bingo` quando completar uma linha.",
            file=_card_file(player, game),
        )
        return True
    except discord.HTTPException:
        return False


def _new_card() -> list[list[int | None]]:
    columns = [random.sample(list(RANGES[letter]), 5) for letter in "BINGO"]
    return [[None if row == 2 and column == 2 else columns[column][row] for column in range(5)] for row in range(5)]


def _find_player(game: BingoGame | None, user_id: int) -> BingoPlayer | None:
    if not game:
        return None
    return next((player for player in game.players if player.member.id == user_id), None)


def _table_embed(game: BingoGame, note: str | None = None) -> discord.Embed:
    embed = discord.Embed(title="Bingo da Ayla", description=note or "Acompanhe os numeros sorteados no painel.", color=0x5865F2)
    embed.add_field(name="Jogadores", value="\n".join(f"{p.member.display_name}: cartela privada" for p in game.players), inline=False)
    embed.add_field(name="Ultimo numero", value=f"`{game.called[-1]}`" if game.called else "Ainda nao sorteado", inline=True)
    embed.add_field(name="Numeros chamados", value=f"`{len(game.called)}/75`", inline=True)
    return embed


def _winner_embed(player: BingoPlayer) -> discord.Embed:
    embed = discord.Embed(title="Bingo finalizado", description=f"{player.member.mention} venceu o Bingo!", color=0x2ECC71)
    embed.add_field(name="Cartela vencedora", value="Linha, coluna ou diagonal completa.")
    return embed


def _card_file(player: BingoPlayer, game: BingoGame, *, winner: bool = False) -> discord.File:
    image = Image.new("RGB", (900, 780), (15, 20, 35))
    draw = ImageDraw.Draw(image)
    title_font = _font(44, True)
    label_font = _font(28, True)
    number_font = _font(32, True)
    draw.text((42, 30), "BINGO DA AYLA", fill=(245, 248, 255), font=title_font)
    draw.text((44, 88), "Cartela vencedora" if winner else "Sua cartela privada", fill=(165, 180, 208), font=label_font)
    left, top, size = 70, 145, 140
    for column, letter in enumerate("BINGO"):
        x = left + column * size
        draw.rounded_rectangle((x, top, x + size - 8, top + 62), radius=12, fill=COLORS[letter])
        _center(draw, letter, (x, top, x + size - 8, top + 62), label_font, (255, 255, 255))
        for row in range(5):
            y = top + 70 + row * 92
            value = player.card[row][column]
            marked = value is None or value in player.marked
            fill = (44, 185, 125) if marked else (35, 45, 68)
            outline = (109, 225, 173) if marked else (85, 100, 130)
            draw.rounded_rectangle((x, y, x + size - 8, y + 82), radius=12, fill=fill, outline=outline, width=3)
            label = "★" if value is None else str(value)
            _center(draw, label, (x, y, x + size - 8, y + 82), number_font, (255, 255, 255))
    draw.text((70, 700), "Verde = marcado  •  O centro é livre", fill=(165, 180, 208), font=_font(22))
    return _file(image, "bingo-cartela.png")


def _board_file(game: BingoGame) -> discord.File:
    image = Image.new("RGB", (900, 260), (15, 20, 35))
    draw = ImageDraw.Draw(image)
    draw.text((42, 28), "BINGO DA AYLA", fill=(245, 248, 255), font=_font(40, True))
    draw.text((44, 82), f"Numeros chamados: {len(game.called)}/75", fill=(165, 180, 208), font=_font(25))
    recent = game.called[-15:]
    for index, number in enumerate(recent):
        x = 54 + (index % 8) * 102
        y = 130 + (index // 8) * 56
        draw.rounded_rectangle((x, y, x + 84, y + 42), radius=10, fill=COLORS["BINGO"[min(4, (number - 1) // 15)]])
        _center(draw, str(number), (x, y, x + 84, y + 42), _font(24, True), (255, 255, 255))
    return _file(image, "bingo-mesa.png")


def _center(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], font, fill) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    draw.text(((box[0] + box[2] - bounds[2]) / 2, (box[1] + box[3] - bounds[3]) / 2 - bounds[1]), text, font=font, fill=fill)


def _font(size: int, bold: bool = False):
    names = (["C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf", "arial.ttf"]
             if bold else ["C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf", "arial.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _file(image: Image.Image, filename: str) -> discord.File:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=filename)
