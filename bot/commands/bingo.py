from __future__ import annotations

import asyncio
import io
import random
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

COLORS = {"B": (67, 132, 235), "I": (224, 92, 92), "N": (55, 176, 112), "G": (235, 170, 58), "O": (158, 91, 205)}
RANGES = {"B": range(1, 16), "I": range(16, 31), "N": range(31, 46), "G": range(46, 61), "O": range(61, 76)}
DEFAULT_DRAW_INTERVAL = 25

@dataclass
class BingoPlayer:
    member: discord.Member | discord.User
    card: list[list[int | None]] = field(default_factory=list)
    marked: set[int] = field(default_factory=set)
    def has_bingo(self) -> bool:
        rows = [set(row) - {None} for row in self.card]
        columns = [{self.card[row][column] for row in range(5)} - {None} for column in range(5)]
        diagonals = [{self.card[i][i] for i in range(5)} - {None}, {self.card[i][4-i] for i in range(5)} - {None}]
        return any(line <= self.marked for line in (*rows, *columns, *diagonals))

class BingoGame:
    def __init__(self, channel_id: int, host: discord.Member | discord.User) -> None:
        self.channel_id, self.host_id, self.host = channel_id, host.id, host
        self.players: list[BingoPlayer] = []
        self.called: list[int] = []
        self.started = self.finished = False
        self.mode, self.draw_interval, self.draw_task = "manual", DEFAULT_DRAW_INTERVAL, None
    def is_host_or_mod(self, user: discord.Member | discord.User) -> bool:
        permissions = getattr(user, "guild_permissions", None)
        return user.id == self.host_id or bool(permissions and permissions.manage_guild)
    def add_player(self, member: discord.Member | discord.User) -> bool:
        if self.started or self.finished or _find_player(self, member.id): return False
        self.players.append(BingoPlayer(member)); return True
    def remove_player(self, user_id: int) -> bool:
        if self.started or self.finished: return False
        before = len(self.players); self.players = [p for p in self.players if p.member.id != user_id]
        return len(self.players) != before
    def start(self) -> None:
        for player in self.players:
            player.card = _new_card(); player.marked = {player.card[2][2]}
        self.called.clear(); self.started, self.finished = True, False
    def draw(self) -> int | None:
        if not self.started or self.finished: return None
        available = [n for n in range(1, 76) if n not in self.called]
        if not available: return None
        number = random.choice(available); self.called.append(number); return number
    def mark(self, user_id: int, number: int) -> tuple[bool, str]:
        player = _find_player(self, user_id)
        if not player: return False, "Voce nao esta nessa partida."
        if number not in self.called: return False, "Esse numero ainda nao foi sorteado."
        if number not in {v for row in player.card for v in row if v is not None}: return False, "Esse numero nao esta na sua cartela."
        if number in player.marked: return False, "Esse numero ja esta marcado."
        player.marked.add(number); return True, f"Numero {number} marcado."
    def claim(self, user_id: int) -> tuple[bool, str, BingoPlayer | None]:
        player = _find_player(self, user_id)
        if not player: return False, "Voce nao esta nessa partida.", None
        if not player.has_bingo(): return False, "Ainda nao ha uma linha completa na sua cartela.", None
        self.finished = True; return True, f"{player.member.mention} fez BINGO!", player

def setup_bingo_commands(bot: commands.Bot) -> None:
    games: dict[int, BingoGame] = {}
    group = app_commands.Group(name="bingo", description="Cria uma mesa de Bingo com botoes.")
    async def create_table(channel, channel_id, host):
        game = BingoGame(channel_id, host); games[channel_id] = game
        await channel.send(embed=_table_embed(game, f"Mesa criada por {host.mention}. Configure e entre usando os botoes abaixo."), view=_lobby_view(game, games, bot))
    @bot.group(name="bingo", invoke_without_command=True)
    async def bingo(ctx: commands.Context):
        await ctx.send("Use a!bingo criar para abrir a mesa; as outras acoes sao feitas pelos botoes do embed.")
    @bingo.command(name="criar")
    async def create(ctx: commands.Context):
        if not ctx.guild: await ctx.send("Bingo precisa ser criado dentro de um servidor."); return
        if ctx.channel.id in games: await ctx.send("Ja existe uma mesa de Bingo neste canal."); return
        await create_table(ctx.channel, ctx.channel.id, ctx.author)
    @group.command(name="criar", description="Abre uma mesa de Bingo configuravel por botoes.")
    async def create_slash(interaction: discord.Interaction):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("Bingo precisa ser criado dentro de um servidor.", ephemeral=True); return
        if interaction.channel.id in games:
            await interaction.response.send_message("Ja existe uma mesa de Bingo neste canal.", ephemeral=True); return
        game = BingoGame(interaction.channel.id, interaction.user); games[interaction.channel.id] = game
        await interaction.response.send_message(embed=_table_embed(game, f"Mesa criada por {interaction.user.mention}. Configure e entre usando os botoes abaixo."), view=_lobby_view(game, games, bot))
    bot.tree.add_command(group)

class LobbyView(discord.ui.View):
    def __init__(self, game, games, bot):
        super().__init__(timeout=None); self.game, self.games, self.bot = game, games, bot
    def allowed(self, interaction): return self.games.get(self.game.channel_id) is self.game and not self.game.started
    @discord.ui.button(label="Entrar na mesa", style=discord.ButtonStyle.success, row=0)
    async def join(self, interaction, button):
        if not self.allowed(interaction): await interaction.response.send_message("Essa mesa nao esta mais recebendo jogadores.", ephemeral=True); return
        if self.game.add_player(interaction.user): await interaction.response.edit_message(embed=_table_embed(self.game, f"{interaction.user.mention} entrou na mesa."), view=_lobby_view(self.game, self.games, self.bot))
        else: await interaction.response.send_message("Voce ja esta na mesa.", ephemeral=True)
    @discord.ui.button(label="Sair da mesa", style=discord.ButtonStyle.secondary, row=0)
    async def leave(self, interaction, button):
        if not self.allowed(interaction): await interaction.response.send_message("Essa mesa nao esta mais recebendo jogadores.", ephemeral=True); return
        if self.game.remove_player(interaction.user.id): await interaction.response.edit_message(embed=_table_embed(self.game, f"{interaction.user.mention} saiu da mesa."), view=_lobby_view(self.game, self.games, self.bot))
        else: await interaction.response.send_message("Voce nao esta na mesa.", ephemeral=True)
    @discord.ui.button(label="Modo manual", style=discord.ButtonStyle.primary, row=1)
    async def manual(self, interaction, button):
        if not self.allowed(interaction): return
        if not self.game.is_host_or_mod(interaction.user): await interaction.response.send_message("Apenas o criador ou moderador pode configurar.", ephemeral=True); return
        self.game.mode = "manual"; await interaction.response.edit_message(embed=_table_embed(self.game, "Modo manual selecionado."), view=_lobby_view(self.game, self.games, self.bot))
    @discord.ui.button(label="Modo automatico", style=discord.ButtonStyle.primary, row=1)
    async def automatic(self, interaction, button):
        if not self.allowed(interaction): return
        if not self.game.is_host_or_mod(interaction.user): await interaction.response.send_message("Apenas o criador ou moderador pode configurar.", ephemeral=True); return
        self.game.mode = "automatico"; await interaction.response.edit_message(embed=_table_embed(self.game, "Modo automatico selecionado."), view=_lobby_view(self.game, self.games, self.bot))
    @discord.ui.button(label="Configurar intervalo", style=discord.ButtonStyle.secondary, row=1)
    async def configure(self, interaction, button):
        if not self.allowed(interaction): return
        if not self.game.is_host_or_mod(interaction.user): await interaction.response.send_message("Apenas o criador ou moderador pode configurar.", ephemeral=True); return
        await interaction.response.send_modal(IntervalModal(self.game, interaction.message, self.games, self.bot))
    @discord.ui.button(label="Iniciar mesa", style=discord.ButtonStyle.success, row=2)
    async def start(self, interaction, button):
        if not self.allowed(interaction): return
        if interaction.user.id != self.game.host_id: await interaction.response.send_message("Apenas quem criou a mesa pode iniciar.", ephemeral=True); return
        if len(self.game.players) < 2: await interaction.response.send_message("Bingo precisa de pelo menos 2 jogadores.", ephemeral=True); return
        self.game.start(); failed = await _dm_all_cards(self.game, self.games, self.bot)
        note = "Mesa iniciada. Os novos numeros serao anunciados aqui e no PV."
        if failed: note += " Nao consegui enviar PV para: " + ", ".join(failed)
        await interaction.response.edit_message(embed=_table_embed(self.game, note), view=_active_view(self.game, self.games, self.bot))
        if self.game.mode == "automatico": self.game.draw_task = asyncio.create_task(_auto_draw(self.game, self.games, self.bot))
    @discord.ui.button(label="Cancelar mesa", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction, button):
        if not self.allowed(interaction): return
        if not self.game.is_host_or_mod(interaction.user): await interaction.response.send_message("Apenas o criador ou moderador pode cancelar.", ephemeral=True); return
        self.games.pop(self.game.channel_id, None); await interaction.response.edit_message(content="Mesa de Bingo cancelada.", embed=None, view=None)

def _lobby_view(game, games, bot): return LobbyView(game, games, bot)

class IntervalModal(discord.ui.Modal, title="Configurar sorteio"):
    seconds = discord.ui.TextInput(label="Segundos entre sorteios", placeholder="25", min_length=1, max_length=4)
    def __init__(self, game, message, games, bot):
        super().__init__(); self.game, self.message, self.games, self.bot = game, message, games, bot
    async def on_submit(self, interaction):
        try: value = int(str(self.seconds.value))
        except ValueError: value = 0
        if not 5 <= value <= 3600: await interaction.response.send_message("Informe um intervalo entre 5 e 3600 segundos.", ephemeral=True); return
        self.game.draw_interval = value; await interaction.response.send_message(f"Intervalo configurado para {value}s.", ephemeral=True)
        await self.message.edit(embed=_table_embed(self.game, "Intervalo atualizado."), view=_lobby_view(self.game, self.games, self.bot))

class ActiveView(discord.ui.View):
    def __init__(self, game, games, bot):
        super().__init__(timeout=None); self.game, self.games, self.bot = game, games, bot
    @discord.ui.button(label="Marcar numero", style=discord.ButtonStyle.primary, row=0)
    async def mark(self, interaction, button):
        player = _find_player(self.game, interaction.user.id)
        if not player: await interaction.response.send_message("Voce nao esta nessa partida.", ephemeral=bool(interaction.guild)); return
        await interaction.response.send_modal(NumberModal(self.game, player, self.games, self.bot))
    @discord.ui.button(label="Minha cartela (PV)", style=discord.ButtonStyle.secondary, row=0)
    async def card(self, interaction, button):
        ok = await _send_card(_find_player(self.game, interaction.user.id), self.game, self.games, self.bot)
        await interaction.response.send_message("Cartela enviada no PV." if ok else "Nao consegui abrir seu PV.", ephemeral=bool(interaction.guild))
    @discord.ui.button(label="Fazer BINGO", style=discord.ButtonStyle.success, row=0)
    async def claim(self, interaction, button):
        ok, message, player = self.game.claim(interaction.user.id)
        if not ok: await interaction.response.send_message(message, ephemeral=bool(interaction.guild)); return
        self.games.pop(self.game.channel_id, None)
        if self.game.draw_task: self.game.draw_task.cancel()
        await interaction.response.send_message(content=message, file=_card_file(player, winner=True), embed=_winner_embed(player))
    @discord.ui.button(label="Sortear agora", style=discord.ButtonStyle.primary, row=1)
    async def draw(self, interaction, button):
        if self.game.mode != "manual": await interaction.response.send_message("A mesa esta no modo automatico.", ephemeral=bool(interaction.guild)); return
        if not self.game.is_host_or_mod(interaction.user): await interaction.response.send_message("Apenas o criador ou moderador pode sortear.", ephemeral=bool(interaction.guild)); return
        number = self.game.draw()
        if number is None: await interaction.response.send_message("Nao ha mais numeros para sortear.", ephemeral=bool(interaction.guild)); return
        await _announce_draw(self.game, number, self.games, self.bot); await interaction.response.send_message(f"Numero {number} anunciado.", ephemeral=bool(interaction.guild))
    @discord.ui.button(label="Cancelar mesa", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction, button):
        if not self.game.is_host_or_mod(interaction.user): await interaction.response.send_message("Apenas o criador ou moderador pode cancelar.", ephemeral=bool(interaction.guild)); return
        self.games.pop(self.game.channel_id, None)
        if self.game.draw_task: self.game.draw_task.cancel()
        await interaction.response.edit_message(content="Mesa de Bingo cancelada.", embed=None, view=None)

def _active_view(game, games, bot): return ActiveView(game, games, bot)

class NumberModal(discord.ui.Modal, title="Marcar numero"):
    number = discord.ui.TextInput(label="Numero da cartela", placeholder="Digite somente o numero", min_length=1, max_length=2)
    def __init__(self, game, player, games, bot):
        super().__init__(); self.game, self.player, self.games, self.bot = game, player, games, bot
    async def on_submit(self, interaction):
        raw = str(self.number.value).strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 75: await interaction.response.send_message("Digite somente um numero entre 1 e 75.", ephemeral=bool(interaction.guild)); return
        ok, message = self.game.mark(self.player.member.id, int(raw))
        if not ok: await interaction.response.send_message(message, ephemeral=bool(interaction.guild)); return
        await _send_card(self.player, self.game, self.games, self.bot); await interaction.response.send_message(message + " Cartela atualizada no PV.", ephemeral=bool(interaction.guild))

async def _auto_draw(game, games, bot):
    try:
        while games.get(game.channel_id) is game and game.started and not game.finished:
            await asyncio.sleep(game.draw_interval); number = game.draw()
            if number is None: return
            await _announce_draw(game, number, games, bot)
    except asyncio.CancelledError: return

async def _announce_draw(game, number, games, bot):
    channel = bot.get_channel(game.channel_id)
    if channel: await channel.send(file=_board_file(game), embed=_table_embed(game, f"Novo numero sorteado: {number}"), view=_active_view(game, games, bot))
    for player in game.players:
        try: await player.member.send(f"Novo numero sorteado no Bingo: {number}. Use o botao Marcar numero para informar somente o numero.")
        except discord.HTTPException: pass

async def _dm_all_cards(game, games, bot):
    failed = []
    for player in game.players:
        if not await _send_card(player, game, games, bot): failed.append(player.member.display_name)
    return failed

async def _send_card(player, game, games, bot):
    if not player: return False
    try:
        await player.member.send("Sua cartela do Bingo da Ayla. Escolha os numeros usando o botao abaixo; vale no PV e no chat.", file=_card_file(player), view=_active_view(game, games, bot)); return True
    except discord.HTTPException: return False

def _new_card():
    columns = [random.sample(list(RANGES[letter]), 5) for letter in "BINGO"]
    return [[None if row == 2 and column == 2 else columns[column][row] for column in range(5)] for row in range(5)]

def _find_player(game, user_id):
    return next((p for p in game.players if p.member.id == user_id), None) if game else None

def _table_embed(game, note=None):
    players = "\n".join(f"{p.member.display_name}: cartela privada" for p in game.players) or "Ninguem entrou ainda."
    embed = discord.Embed(title="Bingo da Ayla", description=note or "Use os botoes para controlar a mesa.", color=0x5865F2)
    embed.add_field(name="Status", value="em andamento" if game.started else "aguardando jogadores", inline=True); embed.add_field(name="Modo", value=game.mode, inline=True); embed.add_field(name="Intervalo", value=f"{game.draw_interval}s", inline=True)
    embed.add_field(name="Jogadores", value=players, inline=False); embed.add_field(name="Ultimo numero", value=str(game.called[-1]) if game.called else "Ainda nao sorteado", inline=True); embed.add_field(name="Numeros chamados", value=f"{len(game.called)}/75", inline=True)
    return embed

def _winner_embed(player):
    return discord.Embed(title="Bingo finalizado", description=f"{player.member.mention} venceu o Bingo!", color=0x2ECC71)

def _card_file(player, winner=False):
    image = Image.new("RGB", (900, 780), (15, 20, 35)); draw = ImageDraw.Draw(image); title_font, label_font, number_font = _font(44, True), _font(28, True), _font(32, True)
    draw.text((42, 30), "BINGO DA AYLA", fill=(245, 248, 255), font=title_font); draw.text((44, 88), "Cartela vencedora" if winner else "Sua cartela privada", fill=(165, 180, 208), font=label_font)
    left, top, size = 70, 145, 140
    for column, letter in enumerate("BINGO"):
        x = left + column * size; draw.rounded_rectangle((x, top, x + size - 8, top + 62), radius=12, fill=COLORS[letter]); _center(draw, letter, (x, top, x + size - 8, top + 62), label_font, (255, 255, 255))
        for row in range(5):
            y = top + 70 + row * 92; value = player.card[row][column]; marked = value is None or value in player.marked
            draw.rounded_rectangle((x, y, x + size - 8, y + 82), radius=12, fill=(44, 185, 125) if marked else (35, 45, 68), outline=(109, 225, 173) if marked else (85, 100, 130), width=3); _center(draw, "*" if value is None else str(value), (x, y, x + size - 8, y + 82), number_font, (255, 255, 255))
    draw.text((70, 700), "Verde = marcado  * O centro e livre", fill=(165, 180, 208), font=_font(22)); return _file(image, "bingo-cartela.png")

def _board_file(game):
    image = Image.new("RGB", (900, 260), (15, 20, 35)); draw = ImageDraw.Draw(image); draw.text((42, 28), "BINGO DA AYLA", fill=(245, 248, 255), font=_font(40, True)); draw.text((44, 82), f"Numeros chamados: {len(game.called)}/75", fill=(165, 180, 208), font=_font(25))
    for index, number in enumerate(game.called[-15:]):
        x, y = 54 + (index % 8) * 102, 130 + (index // 8) * 56; draw.rounded_rectangle((x, y, x + 84, y + 42), radius=10, fill=COLORS["BINGO"[min(4, (number - 1) // 15)]]); _center(draw, str(number), (x, y, x + 84, y + 42), _font(24, True), (255, 255, 255))
    return _file(image, "bingo-mesa.png")

def _center(draw, text, box, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font); draw.text(((box[0] + box[2] - bounds[2]) / 2, (box[1] + box[3] - bounds[3]) / 2 - bounds[1]), text, font=font, fill=fill)

def _font(size, bold=False):
    names = ["C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf", "arial.ttf"] if bold else ["C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf", "arial.ttf"]
    for name in names:
        try: return ImageFont.truetype(name, size)
        except OSError: pass
    return ImageFont.load_default()

def _file(image, filename):
    buffer = io.BytesIO(); image.save(buffer, format="PNG"); buffer.seek(0); return discord.File(buffer, filename=filename)
