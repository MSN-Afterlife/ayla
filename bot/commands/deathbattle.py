from asyncio import wait
import random
from tabnanny import check

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

habilidades = {
    "bola_de_fogo": {
        "nome": "Bola de Fogo",
        "descricao": "{atacante} cria uma grande bola de fogo que explode em {alvo}, causando {dano} de dano!",
        "dano_min": 17,
        "dano_max": 25,
        "mana_gasta": 20,
        "chance_de_acertar": 16,
        "tipo": "fogo",
        "efeitos": None,
        "gif": "https://c.tenor.com/oS1LyI_fYRsAAAAC/tenor.gif"
    },

    "espada_de_fogo": {
        "nome": "Espada de Fogo",
        "descricao": "{atacante} cria uma espada de fogo e a lança em {alvo}, causando {dano} de dano e queimando-o!",
        "dano_min": 20,
        "dano_max": 30,
        "mana_gasta": 32,
        "chance_de_acertar": 15,
        "tipo": "fogo",
        "efeitos": "queimando",
        "gif": "https://c.tenor.com/gSh75BU95fAAAAAC/tenor.gif"
    },

    "sequencia_de_fogo": {
        "nome": "Sequência de Fogo",
        "descricao": "{atacante} ataca com uma sequência de chamas, causando {dano} a {alvo}!",
        "dano_min": 16,
        "dano_max": 40,
        "mana_gasta": 20,
        "chance_de_acertar": 16,
        "tipo": "fogo",
        "efeitos": None,
        "gif": "https://c.tenor.com/6P0wpbRmJu4AAAAC/tenor.gif"
    },

    "fractal_de_gelo": {
        "nome": "Fractal de Gelo",
        "descricao": "{atacante} empala {alvo} com um fractal de gelo, causando {dano} de dano!",
        "dano_min": 10,
        "dano_max": 20,
        "mana_gasta": 10,
        "chance_de_acertar": 14,
        "tipo": "gelo",
        "efeitos": "sangrando",
        "gif": "https://c.tenor.com/ka2IT1FMwfIAAAAC/tenor.gif"
    },

    "era_do_gelo": {
        "nome": "Era do Gelo",
        "descricao": "{atacante} invoca a era do gelo, congelando {alvo} e causando {dano} de dano!",
        "dano_min": 15,
        "dano_max": 20,
        "mana_gasta": 35,
        "chance_de_acertar": 20,
        "tipo": "gelo",
        "efeitos": "paralisado",
        "gif": "https://c.tenor.com/tJsyD_69pWwAAAAC/tenor.gif"
    },

    "chute_enregelado": {
        "nome": "Chute Enregelado",
        "descricao": "{atacante} investe em {alvo} sem piedade e com muita frieza! Causando {dano} de dano!",
        "dano_min": 20,
        "dano_max": 35,
        "mana_gasta": 28,
        "chance_de_acertar": 17,
        "tipo": "gelo",
        "efeitos": None,
        "gif": "https://c.tenor.com/1ShLtkndg60AAAAC/tenor.gif"
    },

    "pistola": {
        "nome": "Pistola",
        "descricao": "{atacante} dispara em {alvo}, causando {dano} de dano!",
        "dano_min": 10,
        "dano_max": 30,
        "mana_gasta": 20,
        "chance_de_acertar": 16,
        "tipo": "atirador",
        "efeitos": "sangrando",
        "gif": "https://c.tenor.com/1ShLtkndg60AAAAC/tenor.gif"
    },

    "metralhadora": {
        "nome": "Metralhadora",
        "descricao": "{atacante} abre fogo contra {alvo}, disparando 7 tiros, causando {dano} de dano!",
        "dano_min": 42,
        "dano_max": 63,
        "mana_gasta": 30,
        "chance_de_acertar": 14,
        "tipo": "atirador",
        "efeitos": "sangrando",
        "gif": "https://c.tenor.com/C3dIGhDlBF8AAAAC/tenor.gif"
    },

    "sniper": {
        "nome": "Sniper",
        "descricao": "{atacante} atira com precisão de sniper em {alvo}, causando {dano} de dano!",
        "dano_min": 25,
        "dano_max": 40,
        "mana_gasta": 25,
        "chance_de_acertar": 19,
        "tipo": "atirador",
        "efeitos": "sangrando",
        "gif": "https://c.tenor.com/0H3MsXseel0AAAAC/tenor.gif"
    },

    "telecinese": {
        "nome": "Telecinese",
        "descricao": "{atacante} utiliza sua habilidade de telecinese em {alvo}, causando {dano} de dano!",
        "dano_min": 20,
        "dano_max": 35,
        "mana_gasta": 28,
        "chance_de_acertar": 17,
        "tipo": "psiquico",
        "efeitos": "paralisado",
        "gif": "https://c.tenor.com/efrfIFHzRLcAAAAd/tenor.gif"
    },

    "raio_psiquico": {
        "nome": "Raio Psíquico",
        "descricao": "{atacante} dispara um raio psíquico em {alvo}, causando {dano} de dano!",
        "dano_min": 15,
        "dano_max": 30,
        "mana_gasta": 17,
        "chance_de_acertar": 16,
        "tipo": "psiquico",
        "efeitos": None,
        "gif": "https://c.tenor.com/hiPDklzsm0kAAAAC/tenor.gif"
    },

    "visao": {
        "nome": "Visão",
        "descricao": "{atacante} usa sua habilidade de visão e prevê o próximo movimento de {alvo}!",
        "dano_min": 0,
        "dano_max": 0,
        "mana_gasta": 30,
        "chance_de_acertar": 100,
        "tipo": "psiquico",
        "efeitos": "previsivel",
        "gif": "https://c.tenor.com/7_LTqjUffAwAAAAC/tenor.gif"
    }
}
condicoes = {
    "queimando": {
        "duracao": 3,
        "dano": 5
    },
    "sangrando": {
        "duracao": 2,
        "dano": 6
    },
    "paralisado": {
        "chance": 50
    },
    "previsivel": {}
}

def encontrar_habilidade(nome):
    nome = nome.lower().strip()

    for habilidade_id, habilidade in habilidades.items():
        if habilidade["nome"].lower() == nome:
            return habilidade_id

    return None

async def esperar_ataque(usuario):
    await usuario.send('# Seu turno\n Se deseja atacar, digite o nome do ataque\n Se deseja socar o oponente, digite soco\n Se deseja se proteger, digite proteger\n Se deseja usar sua **ultimate**, digite ultimate')
    def check(mensagem):
        return (mensagem.author == usuario and isinstance(mensagem.channel, discord.DMChannel))
    while True:
        mensagem = await bot.wait_for("message", check=check)
        texto = mensagem.content.lower().strip()

        if texto == "ultimate":
            return "ultimate"
        elif texto == "soco":
            return "soco"
        elif texto == "proteger":
            return "proteger"

        ataque = encontrar_habilidade(texto)

        if ataque is None:
            await usuario.send("Esse golpe não existe. Digite o nome de um golpe válido.")
            continue
        return ataque

def aplicar_condicao(efeito, condicoes_alvo):
    dados = condicoes[efeito]
    if efeito is None:
        return

    if "duracao" in dados:
        condicoes_alvo[efeito] = dados["duracao"]
    else:
        condicoes_alvo[efeito] = True

def criar_embed_ataque(atacante, alvo, ataque, dano):
    habilidade = habilidades[ataque]
    embed = discord.Embed(
        title=habilidade["nome"],
        description=habilidade["descricao"].format(atacante=atacante.name, alvo=alvo.name, dano=dano),
        color=discord.Color.red()
    )
    embed.set_image(url=habilidade["gif"])
    return embed

async def processar_condicoes(ctx, usuario, vida, condicoes_jogador):
    for efeito in ("queimando", "sangrando"):
        if efeito not in condicoes_jogador:
            continue
        dano = condicoes[efeito]["dano"]
        vida -= dano
        await ctx.send(f"{usuario.mention} sofreu {dano} de dano por estar {efeito}.")
        condicoes_jogador[efeito] -= 1
        if condicoes_jogador[efeito] <= 0:
            del condicoes_jogador[efeito]
            await ctx.send(f"{usuario.mention} não está mais {efeito}.")
    return vida

async def verificar_ult(usuario, turno, ultimate, pronta, usada):
    if turno >= 3 and not pronta and ultimate != "reviver":
        await usuario.send(
            f"Sua ultimate {ultimate} está pronta para uso."
        )
        return True

    return pronta

@bot.command(name="deathbattle")
async def deathbattle(ctx, inimigo: discord.Member):
    if inimigo.bot:
        await ctx.send("Você não pode desafiar um bot.")
        return
    resposta = None
    moveset_jogador = None
    moveset_inimigo = None
    ultimate_jogador = None
    ultimate_inimigo = None
    
    opcoes1 = [discord.SelectOption(label="Moveset de fogo", value="fogo"), discord.SelectOption(label="Moveset de gelo", value="gelo"), discord.SelectOption(label="Moveset de atirador", value="atirador"), discord.SelectOption(label="Moveset de psiquico", value="psiquico")] 
    view_moves1 = discord.ui.View()
    view_moves2 = discord.ui.View()
    select_moves1 = discord.ui.Select(placeholder="Escolha seu moveset", options=opcoes1)
    select_moves2 = discord.ui.Select(placeholder="Escolha seu moveset", options=opcoes1)
    view_moves1.add_item(select_moves1)
    view_moves2.add_item(select_moves2)

    view_acre = discord.ui.View() 
    accept_b = discord.ui.Button(label="Aceitar", style=discord.ButtonStyle.green) 
    reject_b = discord.ui.Button(label="Recusar", style=discord.ButtonStyle.red)
    view_acre.add_item(accept_b) 
    view_acre.add_item(reject_b) 

    opcoes2 = [discord.SelectOption(label="Kirim", value="kirim"), discord.SelectOption(label="Reviver", value="reviver"), discord.SelectOption(label="Sol nascente", value="sol nascente"), discord.SelectOption(label="Meta-vision", value="meta vision")]
    view_ultimate1 = discord.ui.View()
    view_ultimate2 = discord.ui.View()
    select_ultimate1 = discord.ui.Select(placeholder="Escolha sua ultimate", options=opcoes2)
    select_ultimate2 = discord.ui.Select(placeholder="Escolha sua ultimate", options=opcoes2)
    view_ultimate1.add_item(select_ultimate1)
    view_ultimate2.add_item(select_ultimate2)

    async def check_acceptance(interaction):
        nonlocal resposta
        if interaction.user == inimigo:
            await interaction.response.send_message(f"{inimigo.mention} aceitou o desafio!")
            resposta = "aceito"
            view_acre.stop()
        else:
            await interaction.response.send_message("Você não pode aceitar ou recusar este desafio.", ephemeral=True)
            return
    
    async def check_rejection(interaction):
        nonlocal resposta
        if interaction.user == inimigo:
            await interaction.response.send_message(f"{inimigo.mention} recusou o desafio.")
            resposta = "recusado"
            view_acre.stop()
        else:
            await interaction.response.send_message("Você não pode aceitar ou recusar este desafio.", ephemeral=True)
            return

    async def escolher_moveset_jogador(interaction):
        nonlocal moveset_jogador
        moveset_jogador = interaction.data["values"][0]
        view_moves1.stop()
        await interaction.response.send_message(f"{ctx.author.mention} escolheu o moveset {moveset_jogador}.", ephemeral=True)


    async def escolher_moveset_inimigo(interaction):
        nonlocal moveset_inimigo
        moveset_inimigo = interaction.data["values"][0]
        view_moves2.stop()
        await interaction.response.send_message(f"{inimigo.mention} escolheu o moveset {moveset_inimigo}.", ephemeral=True)

    async def escolher_ultimate_jogador(interaction):
        nonlocal ultimate_jogador
        ultimate_jogador = interaction.data["values"][0]
        view_ultimate1.stop()
        await interaction.response.send_message(f"{ctx.author.mention} escolheu a ultimate {ultimate_jogador.capitalize()}.", ephemeral=True)
    
    async def escolher_ultimate_inimigo(interaction):
        nonlocal ultimate_inimigo
        ultimate_inimigo = interaction.data["values"][0]
        view_ultimate2.stop()
        await interaction.response.send_message(f"{inimigo.mention} escolheu a ultimate {ultimate_inimigo.capitalize()}.", ephemeral=True)

    accept_b.callback = check_acceptance
    reject_b.callback = check_rejection
    select_moves1.callback = escolher_moveset_jogador
    select_moves2.callback = escolher_moveset_inimigo
    select_ultimate1.callback = escolher_ultimate_jogador
    select_ultimate2.callback = escolher_ultimate_inimigo
    
    await ctx.send(f"{ctx.author.mention} desafiou {inimigo.mention} para uma batalha!", view=view_acre) 
    await view_acre.wait()
    if resposta == "recusado":
        return
    
    await ctx.author.send("Escolha seu moveset:", view=view_moves1) 
    await inimigo.send("Escolha seu moveset:", view=view_moves2)   
    await view_moves1.wait()
    await view_moves2.wait()
    
    await ctx.author.send(f"Agora, escolha sua ultimate:", view=view_ultimate1)
    await inimigo.send(f"Agora, escolha sua ultimate:", view=view_ultimate2)
    await view_ultimate1.wait()
    await view_ultimate2.wait()

    vida_p1 = 100
    vida_p2 = 100

    mana_p1 = 100
    mana_p2 = 100 

    turno_p1 = 0
    turno_p2 = 0

    condicoes_p1 = {}
    condicoes_p2 = {}
    
    await ctx.send(f"A batalha começou entre {ctx.author.mention} e {inimigo.mention}!\n\n{ctx.author.mention} - Vida: {vida_p1}, Mana: {mana_p1}\n{inimigo.mention} - Vida: {vida_p2}, Mana: {mana_p2}")

    iniciativa_p1 = random.randint(1, 20)
    iniciativa_p2 = random.randint(1, 20)

    if iniciativa_p1 > iniciativa_p2:
        primeiro = (ctx.author, inimigo, moveset_jogador, ultimate_jogador)
        segundo = (inimigo, ctx.author, moveset_inimigo, ultimate_inimigo)

    elif iniciativa_p2 > iniciativa_p1:
        primeiro = (inimigo, ctx.author, moveset_inimigo, ultimate_inimigo)
        segundo = (ctx.author, inimigo, moveset_jogador, ultimate_jogador)

    else:
        primeiro = (ctx.author, inimigo, moveset_jogador, ultimate_jogador)
        segundo = (inimigo, ctx.author, moveset_inimigo, ultimate_inimigo)

    ultimate_pronta_p1 = False
    ultimate_pronta_p2 = False

    ultimate_usada_p1 = False
    ultimate_usada_p2 = False

    p1_protegendo = False
    p2_protegendo = True

    await ctx.send(f"{ctx.author.mention} tirou {iniciativa_p1} na iniciativa\n" f"{inimigo.mention} tirou {iniciativa_p2} na iniciativa\n\n" f"{primeiro[0].mention} começa atacando!")

    while vida_p1 > 0 and vida_p2 > 0:
        for atacante, alvo, moveset, ultimate in (primeiro, segundo):
            if atacante == ctx.author:
                vida_p1 = await processar_condicoes(ctx,atacante,vida_p1,condicoes_p1)
                condicoes_atual = condicoes_p1
            else:
                vida_p2 = await processar_condicoes(ctx,atacante,vida_p2,condicoes_p2)
                condicoes_atual = condicoes_p2
            ataque = await esperar_ataque(atacante)
            if alvo == inimigo:
                turno_p1 += 1
                if ultimate_inimigo == "sol nascente" and ultimate_usada_p2 == True:
                    vida_p1 -= 20
                    await ctx.send("☀ O sol criado pelo oponente torra {atacante}, causando 20 de dano")
            else:
                turno_p2 += 1
                if ultimate_jogador == "sol nascente" and ultimate_usada_p1 == True:
                    vida_p2 -= 20
                    await ctx.send("☀ O sol criado pelo oponente torra {atacante}, causando 20 de dano")
            
            if ataque == "ultimate":
                if atacante == ctx.author:
                    if turno_p1 < 3 or ultimate_usada_p1 == True or ultimate_jogador == "reviver":
                        await atacante.send("Você não pode usar sua utlimate")
                        while True:
                            ataque = await esperar_ataque(atacante)
                            if ataque != "ultimate":
                                break
                            if atacante == ctx.author:
                                pronta = ultimate_pronta_p1
                                usada = ultimate_usada_p1
                                ultimate_escolhida = ultimate_jogador
                            else:
                                pronta = ultimate_pronta_p2
                                usada = ultimate_usada_p2
                                ultimate_escolhida = ultimate_inimigo

                            if ultimate_escolhida == "reviver":
                                await atacante.send(
                                    "Reviver é ativada automaticamente quando você morre."
                                )
                                continue

                            if not pronta:
                                await atacante.send("Sua ultimate ainda não está pronta.")
                                continue

                            if usada:
                                await atacante.send("Você já usou sua ultimate.")
                                continue
                            break
                    else:
                        if ultimate_jogador == "kirim":
                            mana_p1 = 0
                            if random.randint(1, 20) < 2:
                                await ctx.send(f"{atacante} usou toda sua mana para invocar Kirim, mas errou!")
                            else:
                                dano = random.randint(60, 80)
                                if p2_protegendo == True:
                                    vida_p2 -= dano - 15
                                else:
                                    vida_p2 -= dano
                                embed = discord.Embed(
                                    title="Kirim",
                                    description=f"{atacante} Gastou toda sua mana usando Kirim, e causou {dano}",
                                    color=discord.Color.blue()
                                )
                                embed.set_image(url="https://c.tenor.com/I-STQyYk1QsAAAAC/tenor.gif")
                                await ctx.send(embed=embed)
                        elif ultimate_jogador == "sol nascente":
                            if mana_p1 >= 20:
                                mana_p1 -= 20
                                embed = discord.Embed(
                                    title="Sol nascente",
                                    description=f"{atacante} invocou um sol nascente na arena de batalha, será que o oponente suporta o calor?",
                                    color=discord.Color.yellow()
                                )
                                embed.set_image(url="https://c.tenor.com/kRg2pd68FHEAAAAd/tenor.gif")
                                await ctx.send(embed=embed)
                        elif ultimate_jogador == "meta vision":
                            if mana_p1 >= 35:
                                mana_p1 -= 35
                                embed = discord.Embed(
                                    title="Meta vision",
                                    description=f"{atacante} começa a ver o campo de batalha de forma diferente... agora para acerta-lo será necessário o dobro de esforço!",
                                    color=discord.Color.light_gray()
                                )
                                embed.set_image(url="https://c.tenor.com/08nSzJF021EAAAAd/tenor.gif")
                                await ctx.send(embed=embed)
                else: 
                    if turno_p2 < 3 or ultimate_usada_p2 == True or ultimate_inimigo == "reviver":
                        await atacante.send("Você não pode usar sua utlimate")
                        while True:
                            ataque = await esperar_ataque(atacante)
                            if ataque != "ultimate":
                                break
                            if atacante == ctx.author:
                                pronta = ultimate_pronta_p1
                                usada = ultimate_usada_p1
                                ultimate_escolhida = ultimate_jogador
                            else:
                                pronta = ultimate_pronta_p2
                                usada = ultimate_usada_p2
                                ultimate_escolhida = ultimate_inimigo

                            if ultimate_escolhida == "reviver":
                                await atacante.send(
                                    "Reviver é ativada automaticamente quando você morre."
                                )
                                continue

                            if not pronta:
                                await atacante.send("Sua ultimate ainda não está pronta.")
                                continue

                            if usada:
                                await atacante.send("Você já usou sua ultimate.")
                                continue
                            break
                    else:
                        if ultimate_inimigo == "kirim":
                            mana_p2 = 0
                            if random.randint(1, 20) < 2:
                                await ctx.send(f"{atacante} usou toda sua mana para invocar Kirim, mas errou!")
                            else:
                                dano = random.randint(60, 80)
                                if p1_protegendo == True:
                                    vida_p1 -= dano - 15
                                else:
                                    vida_p1 -= dano
                                embed = discord.Embed(
                                    title="Kirim",
                                    description=f"{atacante} Gastou toda sua mana usando Kirim, e causou {dano}",
                                    color=discord.Color.blue()
                                )
                                embed.set_image(url="https://c.tenor.com/I-STQyYk1QsAAAAC/tenor.gif")
                                await ctx.send(embed=embed)
                        elif ultimate_inimigo == "sol nascente":
                            if mana_p2 >= 20:
                                mana_p2 -= 20
                                embed = discord.Embed(
                                    title="Sol nascente",
                                    description=f"{atacante} invocou um sol nascente na arena de batalha, será que o oponente suporta o calor?",
                                    color=discord.Color.yellow()
                                )
                                embed.set_image(url="https://c.tenor.com/kRg2pd68FHEAAAAd/tenor.gif")
                                await ctx.send(embed=embed)
                        elif ultimate_inimigo == "meta vision":
                            if mana_p2 >= 35:
                                mana_p2 -= 35
                                embed = discord.Embed(
                                    title="Meta vision",
                                    description=f"{atacante} começa a ver o campo de batalha de forma diferente... agora para acerta-lo será necessário o dobro de esforço!",
                                    color=discord.Color.light_gray()
                                )
                                embed.set_image(url="https://c.tenor.com/08nSzJF021EAAAAd/tenor.gif")
                                await ctx.send(embed=embed)
            elif ataque == "soco":
                if atacante == ctx.author:
                    dano = random.randint(5, 15)
                    if p2_protegendo == True:
                        vida_p2 == vida_p2
                    else:
                        vida_p2 -= dano
                        await ctx.send(f"{atacante} socou {alvo}, causando {dano}")
                else:
                    dano = random.randint(5, 15)
                    if p1_protegendo == True:
                        vida_p1 == vida_p1
                    else:
                        vida_p1 -= dano
                        await ctx.send(f"{atacante} socou {alvo}, causando {dano}!")
            elif ataque == "proteger":
                if atacante == ctx.author:
                    p1_protegendo = True
                    await ctx.send(f"{atacante} se protegeu!")
                else:
                    p2_protegendo = True
                    await ctx.send(f"{atacante} se protegeu!")

            else:
                if habilidades[ataque]["tipo"] != moveset:
                    await atacante.send(f"❌ O golpe **{habilidades[ataque]['nome']}** " f"não pertence ao seu moveset de **{moveset}**.")
                    continue
                mana_atual = mana_p1 if atacante == ctx.author else mana_p2
                if mana_atual < habilidades[ataque]["mana_gasta"]:
                    await atacante.send(f"❌ Você não tem mana suficiente! " f"Você possui **{mana_atual}** de mana.")
                    continue

                mana_gasta = habilidades[ataque]["mana_gasta"]
                if atacante == ctx.author:
                    mana_p1 -= mana_gasta
                    await verificar_ult(atacante, turno_p1, ultimate_jogador, ultimate_pronta_p1, ultimate_usada_p1)
                    if "paralisado" in condicoes_p1:
                        if random.randint(1, 2) == 1:
                            await ctx.send(f"{atacante.mention} ficou paralisado e perdeu o ataque.")
                            del condicoes_p1["paralisado"]
                            ataque_perdido = True
                        else:
                            del condicoes_p1["paralisado"]
                            ataque_perdido = False
                    if "previsivel" in condicoes_p1:
                        await ctx.send(f"{atacante.mention} estava previsível e errou o ataque.")
                        del condicoes_p1["previsivel"]
                        ataque_perdido = True            
                else:
                    mana_p2 -= mana_gasta
                    await verificar_ult(atacante, turno_p2, ultimate_inimigo, ultimate_pronta_p2, ultimate_usada_p2)
                    if "paralisado" in condicoes_p2:
                        if random.randint(1,2) == 1:
                            await ctx.send(f"{atacante.mention} ficou paralisado e perdeu o ataque.")
                            del condicoes_p1["paralisado"]
                            ataque_perdido = True
                        else:
                            del condicoes_p1["paralisado"]
                            ataque_perdido = True
                    if "previsivel" in condicoes_p2:
                        await ctx.send(f"{atacante.mention} estava previsível e errou o ataque.")
                        del condicoes_p1["previsivel"]
                        ataque_perdido = True      

                d20 = random.randint(1, 20)
                acerto = habilidades[ataque]["chance_de_acertar"]
                if alvo == inimigo:
                    if ultimate_inimigo == "meta vision" and ultimate_usada_p2 == True:
                        acerto = habilidades[ataque]["chance_de_acertar"]/2
                else:
                    if ultimate_jogador == "metavision" and ultimate_usada_p1 == True:
                        acerto = habilidades[ataque]["chance_de_acertar"]/2

                if d20 > acerto:
                    await ctx.send(f"{atacante.mention} tentou usar " f"**{habilidades[ataque]['nome']}**, mas errou!")
                else:
                    dano_ataque = random.randint(habilidades[ataque]["dano_min"], habilidades[ataque]["dano_max"])
                    if alvo == ctx.author:
                        if p1_protegendo:
                            vida_p1 -= dano_ataque - 15
                        else:
                            vida_p1 -= dano_ataque
                    else:
                        if p2_protegendo:
                            vida_p2 -= dano_ataque - 15
                        else:
                            vida_p2 -= dano_ataque
                    embed = criar_embed_ataque(atacante, alvo, ataque, dano_ataque)
                    await ctx.send(embed=embed)
                    efeito = habilidades[ataque]["efeitos"]

                    if efeito is not None:
                        if alvo == ctx.author:
                            aplicar_condicao(efeito, condicoes_p1)
                        else:
                            aplicar_condicao(efeito, condicoes_p2)

                    await ctx.send(f"{alvo.mention} recebeu a condição {efeito}.")
    if vida_p1 <= 0:
        if ultimate == "reviver":
            vida_p1 = 50
            ultimate_usada_p1 = True
            embed = discord.Embed(
                title="Ressurreição",
                description=f"{alvo} Morreu, mas voltou a vida graciosamente!",
                color=discord.Color.gold()
            )
            embed.set_image(url="https://c.tenor.com/F7hviGTMsTcAAAAC/tenor.gif")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{ctx.author.mention} morreu! " f"{inimigo.mention} venceu a batalha!")
    elif vida_p2 <= 0:
        if ultimate == "reviver":
            vida_p2 = 50
            ultimate_usada_p2 = True
            embed = discord.Embed(
                title="Ressurreição",
                description=f"{alvo} Morreu, mas voltou a vida graciosamente!",
                color=discord.Color.gold()
            )
            embed.set_image(url="https://c.tenor.com/F7hviGTMsTcAAAAC/tenor.gif")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"{inimigo.mention} morreu! " f"{ctx.author.mention} venceu a batalha!")

def setup_deathbattle_commands(target_bot: commands.Bot) -> None:
    global bot
    bot = target_bot
    target_bot.add_command(deathbattle)
