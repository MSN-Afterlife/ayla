from __future__ import annotations
import asyncio, json
import discord
from discord import app_commands
from discord.ext import commands
from bot.services.bingo_service import BingoError, BingoService
from bot.services.bingo_renderer import render_card

def setup_bingo_commands(bot: commands.Bot) -> None:
    service=BingoService(bot._settings.levels_database_path); bot._bingo_service=service; tasks={}
    async def create(ctx, entry=500):
        if not ctx.guild: return await ctx.send("O Bingo precisa acontecer em um servidor.")
        try: gid=service.create(ctx.guild.id,ctx.channel.id,ctx.author.id,entry)
        except BingoError as e: return await ctx.send(str(e))
        msg=await ctx.send(embed=embed(service.game(game_id=gid)),view=LobbyView(service,None,tasks)); service.set_message(gid,msg.id); await msg.edit(view=LobbyView(service,msg,tasks))
    @bot.group(name="bingo",invoke_without_command=True)
    async def bingo(ctx): await ctx.send(f"Use `{bot.command_prefix}bingo criar 500` ou `/bingo criar`.")
    @bingo.command(name="criar")
    async def criar(ctx, entrada:int=500): await create(ctx,entrada)
    @bingo.command(name="entrar")
    async def entrar(ctx): await action(service,ctx,"join")
    @bingo.command(name="sair")
    async def sair(ctx): await action(service,ctx,"leave")
    @bingo.command(name="cartela")
    async def cartela(ctx): await action(service,ctx,"card")
    @bingo.command(name="marcar")
    async def marcar(ctx,numero:int): await action(service,ctx,"mark",numero)
    @bingo.command(name="desmarcar")
    async def desmarcar(ctx,numero:int): await action(service,ctx,"unmark",numero)
    @bingo.command(name="bingo")
    async def declarar(ctx): await action(service,ctx,"claim",tasks=tasks)
    @bingo.command(name="status")
    async def status(ctx): await ctx.send(embed=embed(service.game(channel_id=ctx.channel.id)))
    @bingo.command(name="cancelar")
    async def cancelar(ctx):
        g=service.game(channel_id=ctx.channel.id)
        try: service.cancel(g["game_id"],ctx.author.id); await ctx.send("Sala cancelada e entradas devolvidas.")
        except (BingoError,TypeError) as e: await ctx.send(str(e))
    group=app_commands.Group(name="bingo",description="Bingo competitivo da Ayla")
    @group.command(name="criar")
    async def s_criar(i:discord.Interaction,entrada:int=500):
        if not i.guild: return await i.response.send_message("O Bingo precisa acontecer em um servidor.",ephemeral=True)
        try: gid=service.create(i.guild.id,i.channel.id,i.user.id,entrada); await i.response.send_message(embed=embed(service.game(game_id=gid)),view=LobbyView(service,None,tasks)); service.set_message(gid,i.original_response().id if False else 0)
        except BingoError as e: await i.response.send_message(str(e),ephemeral=True)
    @group.command(name="entrar")
    async def s_entrar(i:discord.Interaction): await action(service,i,"join")
    @group.command(name="cartela")
    async def s_cartela(i:discord.Interaction): await action(service,i,"card")
    @group.command(name="status")
    async def s_status(i:discord.Interaction): await action(service,i,"status")
    @group.command(name="cancelar")
    async def s_cancelar(i:discord.Interaction): await action(service,i,"cancel")
    @group.command(name="marcar")
    async def s_marcar(i:discord.Interaction,numero:int): await action(service,i,"mark",numero)
    @group.command(name="desmarcar")
    async def s_desmarcar(i:discord.Interaction,numero:int): await action(service,i,"unmark",numero)
    @group.command(name="bingo")
    async def s_bingo(i:discord.Interaction): await action(service,i,"claim",tasks=tasks)
    bot.tree.add_command(group)
    async def restore_bingo():
        for row in service.recoverable():
            channel=bot.get_channel(row["channel_id"])
            if not channel or not row["message_id"]: continue
            try: message=await channel.fetch_message(row["message_id"])
            except discord.HTTPException: continue
            bot.add_view(GameView(service,message,tasks),message_id=row["message_id"])
            tasks[row["game_id"]]=asyncio.create_task(draw_loop(service,message,tasks,row["game_id"]))
    bot.add_listener(restore_bingo,"on_ready")

class LobbyView(discord.ui.View):
    def __init__(self,service,message,tasks): super().__init__(timeout=None); self.service=service; self.message=message; self.tasks=tasks
    async def go(self,i,kind):
        g=self.service.game(channel_id=i.channel.id)
        try:
            if kind=="join": self.service.join(g["game_id"],i.user.id); msg="Você entrou! Sua cartela foi reservada."
            else:
                self.service.start(g["game_id"],i.user.id); msg="🎱 O Bingo começou!"; self.tasks[g["game_id"]]=asyncio.create_task(draw_loop(self.service,self.message,self.tasks,g["game_id"]))
            await i.response.send_message(msg,ephemeral=True); await self.message.edit(embed=embed(self.service.game(game_id=g["game_id"])),view=GameView(self.service,self.message,self.tasks) if kind=="start" else self)
        except (BingoError,TypeError) as e: await i.response.send_message(str(e),ephemeral=True)
    @discord.ui.button(label="🎟️ Entrar",style=discord.ButtonStyle.success)
    async def join(self,i,b): await self.go(i,"join")
    @discord.ui.button(label="▶️ Iniciar",style=discord.ButtonStyle.primary,custom_id="ayla_bingo_start")
    async def start(self,i,b): await self.go(i,"start")
    @discord.ui.button(label="🚪 Cancelar",style=discord.ButtonStyle.danger,custom_id="ayla_bingo_cancel")
    async def cancel(self,i,b):
        try: self.service.cancel(self.service.game(channel_id=i.channel.id)["game_id"],i.user.id); await i.response.edit_message(content="Sala cancelada e entradas devolvidas.",embed=None,view=None)
        except BingoError as e: await i.response.send_message(str(e),ephemeral=True)

class GameView(discord.ui.View):
    def __init__(self,service,message,tasks): super().__init__(timeout=None); self.service=service; self.message=message; self.tasks=tasks
    @discord.ui.button(label="🎴 Minha cartela",style=discord.ButtonStyle.secondary,custom_id="ayla_bingo_card")
    async def card(self,i,b): await action(self.service,i,"card")
    @discord.ui.button(label="✏️ Marcar",style=discord.ButtonStyle.primary,custom_id="ayla_bingo_mark")
    async def mark(self,i,b): await i.response.send_modal(NumberModal(self.service))
    @discord.ui.button(label="🔔 BINGO!",style=discord.ButtonStyle.success,custom_id="ayla_bingo_claim")
    async def claim(self,i,b): await action(self.service,i,"claim",tasks=self.tasks)

class NumberModal(discord.ui.Modal,title="Marcar numero"):
    number=discord.ui.TextInput(label="Numero sorteado",max_length=2)
    def __init__(self,service): super().__init__(); self.service=service
    async def on_submit(self,i):
        try: await action(self.service,i,"mark",int(str(self.number.value)))
        except ValueError: await i.response.send_message("Digite um número válido.",ephemeral=True)

async def draw_loop(service,message,tasks,gid):
    try:
        while True:
            await asyncio.sleep(10); number=service.draw(gid)
            if number is None: return
            await message.edit(embed=embed(service.game(game_id=gid),f"🔴 Saiu o número **{number}**!"),view=GameView(service,message,tasks))
    except (asyncio.CancelledError,discord.HTTPException): return

async def action(service,target,kind,number=None,tasks=None):
    interaction=isinstance(target,discord.Interaction); channel=target.channel; user=target.user if interaction else target.author; g=service.game(channel_id=channel.id)
    try:
        if not g: raise BingoError("Não há uma sala neste canal.")
        if kind=="join": service.join(g["game_id"],user.id); text="🎟️ Você entrou no Bingo!"
        elif kind=="leave": service.leave(g["game_id"],user.id); text="Você saiu e recebeu a entrada de volta."
        elif kind=="cancel": service.cancel(g["game_id"],user.id); text="Sala cancelada e entradas devolvidas."
        elif kind in ("mark","unmark"): service.mark(g["game_id"],user.id,number,kind=="mark"); text="Cartela atualizada!"
        elif kind=="card":
            file=discord.File(render_card(service.card(g["game_id"],user.id)),filename="cartela.png"); return await (target.response.send_message(file=file,ephemeral=True) if interaction else target.send(file=file))
        elif kind=="claim":
            prize,card=service.claim(g["game_id"],user.id); tasks.get(g["game_id"]) and tasks[g["game_id"]].cancel(); file=discord.File(render_card(card,"🏆 CARTELA VENCEDORA"),filename="bingo-vencedor.png"); text=f"🔔 BINGO!!! {user.mention} venceu e levou **{prize} winks**!"; return await (target.response.send_message(text,file=file,ephemeral=False) if interaction else target.send(text,file=file))
        else: text="Estado consultado."
    except BingoError as e: text=str(e)
    return await (target.response.send_message(text,ephemeral=True) if interaction else target.send(text))

def embed(game,note=None):
    if not game: return discord.Embed(title="🎱 BINGO DA AYLA",description="Sala encerrada.")
    drawn=json.loads(game["drawn"]); return discord.Embed(title="🎱 BINGO DA AYLA",description=note or "Entre para receber sua cartela individual.",color=0x20A464).add_field(name="Entrada",value=f"{game['entry']} winks").add_field(name="Prêmio atual",value=f"{game['pot']-game['pot']*5//100} winks").add_field(name="Estado",value=game["state"]).add_field(name="Sorteados",value=f"{len(drawn)}/75")
