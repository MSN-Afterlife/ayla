import random

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
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
    nome = nome.casefold().strip()
    for habilidade_id, habilidade in habilidades.items():
        if habilidade["nome"].casefold() == nome or habilidade_id == nome:
            return habilidade_id
    return None


async def esperar_ataque(usuario, mana_atual, moveset):
    await usuario.send(
        "# Seu turno\n Se quiser usar um ataquem, digite o nome dele\n Se quiser socar seu oponente, digite soco\n Se quiser se proteger, digite proteger\n Se quiser usar sua **ultimate** (disponível apenas depois de 3 turnos), digite ultimate."
    )

    def check(mensagem):
        return mensagem.author == usuario and isinstance(mensagem.channel, discord.DMChannel)

    while True:
        mensagem = await bot.wait_for("message", check=check)
        texto = mensagem.content.casefold().strip()

        if texto in {"ultimate", "soco", "proteger"}:
            return texto

        ataque = encontrar_habilidade(texto)
        if ataque is None:
            await usuario.send("Esse golpe não existe. Digite o nome de um golpe válido.")
            continue
        habilidade = habilidades[ataque]
        if habilidade["tipo"] != moveset:
            await usuario.send(f"O golpe **{habilidade['nome']}** não pertence ao seu moveset de **{moveset}**.")
            continue
        if mana_atual < habilidade["mana_gasta"]:
            await usuario.send(f"Você não tem mana suficiente. Mana atual: **{mana_atual}**.")
            continue
        return ataque


def aplicar_condicao(efeito, condicoes_alvo):
    if efeito is None:
        return
    dados = condicoes.get(efeito)
    if dados is None:
        return
    if "duracao" in dados:
        condicoes_alvo[efeito] = dados["duracao"]
    else:
        condicoes_alvo[efeito] = True


def criar_embed_ataque(atacante, alvo, ataque, dano):
    habilidade = habilidades[ataque]
    embed = discord.Embed(
        title=habilidade["nome"],
        description=habilidade["descricao"].format(
            atacante=atacante.display_name, alvo=alvo.display_name, dano=dano
        ),
        color=discord.Color.red(),
    )
    if habilidade.get("gif"):
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
        await usuario.send(f"Sua ultimate **{ultimate.replace('_', ' ').title()}** está pronta para uso.")
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

    opcoes2 = [discord.SelectOption(label="Kirim", value="kirim"), discord.SelectOption(label="Reviver", value="reviver"), discord.SelectOption(label="Sol nascente", value="sol_nascente"), discord.SelectOption(label="Meta-vision", value="meta_vision")]
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
    p2_protegendo = False

    await ctx.send(f"{ctx.author.mention} tirou {iniciativa_p1} na iniciativa\n" f"{inimigo.mention} tirou {iniciativa_p2} na iniciativa\n\n" f"{primeiro[0].mention} começa atacando!")

    jogadores = {
        ctx.author: {
            "vida": vida_p1, "mana": mana_p1, "turnos": 0,
            "moveset": moveset_jogador, "ultimate": ultimate_jogador,
            "ultimate_pronta": False, "ultimate_usada": False,
            "condicoes": condicoes_p1, "protegendo": False,
            "sol_nascente": False,
        },
        inimigo: {
            "vida": vida_p2, "mana": mana_p2, "turnos": 0,
            "moveset": moveset_inimigo, "ultimate": ultimate_inimigo,
            "ultimate_pronta": False, "ultimate_usada": False,
            "condicoes": condicoes_p2, "protegendo": False,
            "sol_nascente": False,
        },
    }

    def estado(usuario):
        return jogadores[usuario]

    async def verificar_fim():
        for usuario, dados in jogadores.items():
            if dados["vida"] > 0:
                continue
            if dados["ultimate"] == "reviver" and not dados["ultimate_usada"]:
                dados["vida"] = 50
                dados["ultimate_usada"] = True
                embed = discord.Embed(
                    title="Ressurreição",
                    description=f"{usuario.mention} morreu, mas voltou à vida!",
                    color=discord.Color.gold(),
                )
                embed.set_image(url="https://c.tenor.com/F7hviGTMsTcAAAAC/tenor.gif")
                await ctx.send(embed=embed)
                continue
            vencedor = inimigo if usuario == ctx.author else ctx.author
            await ctx.send(f"{usuario.mention} morreu! {vencedor.mention} venceu a batalha!")
            return True
        return False

    async def usar_ultimate(atacante, alvo, dados, dados_alvo):
        nome = dados["ultimate"]
        if nome == "reviver":
            await atacante.send("Reviver é ativada automaticamente quando você morre.")
            return False
        if not dados["ultimate_pronta"]:
            await atacante.send("Sua ultimate ainda não está pronta.")
            return False
        if dados["ultimate_usada"]:
            await atacante.send("Você já usou sua ultimate.")
            return False

        if nome == "kirim":
            dados["mana"] = 0
            dados["ultimate_usada"] = True
            if random.randint(1, 20) == 1:
                await ctx.send(f"{atacante.mention} invocou Kirim, mas errou!")
                return True
            dano = random.randint(60, 80)
            dano_real = max(0, dano - (15 if dados_alvo["protegendo"] else 0))
            dados_alvo["vida"] -= dano_real
            embed = discord.Embed(
                title="Kirim",
                description=f"{atacante.mention} gastou toda sua mana usando Kirim e causou {dano_real} de dano!",
                color=discord.Color.blue(),
            )
            embed.set_image(url="https://c.tenor.com/I-STQyYk1QsAAAAC/tenor.gif")
            await ctx.send(embed=embed)
        elif nome == "sol_nascente":
            if dados["mana"] < 20:
                await atacante.send("Você precisa de 20 de mana para usar Sol Nascente.")
                return False
            dados["mana"] -= 20
            dados["sol_nascente"] = True
            dados["ultimate_usada"] = True
            embed = discord.Embed(
                title="Sol Nascente",
                description=f"{atacante.mention} invocou um sol nascente. O oponente sofrerá 20 de dano no começo de cada turno.",
                color=discord.Color.yellow(),
            )
            embed.set_image(url="https://c.tenor.com/kRg2pd68FHEAAAAd/tenor.gif")
            await ctx.send(embed=embed)
        elif nome == "meta_vision":
            if dados["mana"] < 35:
                await atacante.send("Você precisa de 35 de mana para usar Meta-Vision.")
                return False
            dados["mana"] -= 35
            dados["ultimate_usada"] = True
            dados["meta_vision"] = True
            embed = discord.Embed(
                title="Meta-Vision",
                description=f"{atacante.mention} ativou Meta-Vision: os ataques contra ele ficam mais difíceis de acertar.",
                color=discord.Color.light_grey(),
            )
            embed.set_image(url="https://c.tenor.com/08nSzJF021EAAAAd/tenor.gif")
            await ctx.send(embed=embed)
        return True

    while jogadores[ctx.author]["vida"] > 0 and jogadores[inimigo]["vida"] > 0:
        for atacante, alvo, _, _ in (primeiro, segundo):
            dados = estado(atacante)
            dados_alvo = estado(alvo)
            if await verificar_fim():
                return

            # Efeitos de dano no começo do turno.
            dados["vida"] = await processar_condicoes(ctx, atacante, dados["vida"], dados["condicoes"])
            if dados_alvo["sol_nascente"]:
                dados["vida"] -= 20
                await ctx.send(f"O Sol Nascente de {alvo.mention} causa 20 de dano a {atacante.mention}.")
            if await verificar_fim():
                return

            # Escolha e validação: uma ação inválida não consome o turno.
            while True:
                ataque = await esperar_ataque(atacante, dados["mana"], dados["moveset"])
                if ataque != "ultimate":
                    break
                if dados["ultimate"] == "reviver":
                    await atacante.send("Reviver é ativada automaticamente quando você morre.")
                    continue
                if not dados["ultimate_pronta"]:
                    await atacante.send("Sua ultimate ainda não está pronta.")
                    continue
                if dados["ultimate_usada"]:
                    await atacante.send("Você já usou sua ultimate.")
                    continue
                custo = 20 if dados["ultimate"] == "sol_nascente" else 35 if dados["ultimate"] == "meta_vision" else 0
                if dados["mana"] < custo:
                    await atacante.send(f"Você precisa de {custo} de mana para usar essa ultimate.")
                    continue
                break

            acao_valida = True
            if ataque == "ultimate":
                acao_valida = await usar_ultimate(atacante, alvo, dados, dados_alvo)
            elif ataque == "proteger":
                dados["protegendo"] = True
                await ctx.send(f"{atacante.mention} se protegeu. O próximo golpe recebido terá 15 de dano reduzido.")
            elif ataque == "soco":
                dano = random.randint(5, 15)
                dano_real = max(0, dano - (15 if dados_alvo["protegendo"] else 0))
                dados_alvo["vida"] -= dano_real
                dados_alvo["protegendo"] = False
                await ctx.send(f"{atacante.mention} socou {alvo.mention}, causando {dano_real} de dano.")
            else:
                habilidade = habilidades[ataque]
                dados["mana"] -= habilidade["mana_gasta"]
                ataque_perdido = False
                if "paralisado" in dados["condicoes"]:
                    del dados["condicoes"]["paralisado"]
                    if random.randint(1, 2) == 1:
                        await ctx.send(f"{atacante.mention} ficou paralisado e perdeu o ataque.")
                        ataque_perdido = True
                if "previsivel" in dados["condicoes"]:
                    del dados["condicoes"]["previsivel"]
                    await ctx.send(f"{atacante.mention} estava previsível e errou o ataque.")
                    ataque_perdido = True

                if not ataque_perdido:
                    chance = habilidade["chance_de_acertar"]
                    if dados_alvo.get("meta_vision", False):
                        chance /= 2
                    d20 = random.randint(1, 20)
                    if d20 > chance:
                        await ctx.send(f"{atacante.mention} tentou usar **{habilidade['nome']}**, mas errou (d20: {d20}).")
                    else:
                        dano = random.randint(habilidade["dano_min"], habilidade["dano_max"])
                        dano_real = max(0, dano - (15 if dados_alvo["protegendo"] else 0))
                        dados_alvo["vida"] -= dano_real
                        dados_alvo["protegendo"] = False
                        await ctx.send(embed=criar_embed_ataque(atacante, alvo, ataque, dano_real))
                        efeito = habilidade["efeitos"]
                        if efeito:
                            aplicar_condicao(efeito, dados_alvo["condicoes"])
                            await ctx.send(f"{alvo.mention} recebeu a condição **{efeito}**.")

            if not acao_valida:
                continue

            dados["turnos"] += 1
            dados["ultimate_pronta"] = await verificar_ult(
                atacante, dados["turnos"], dados["ultimate"],
                dados["ultimate_pronta"], dados["ultimate_usada"]
            )
            if await verificar_fim():
                return

            # Consome a proteção após ter absorvido um golpe; caso contrário ela persiste.
            if jogadores[ctx.author]["vida"] <= 0 or jogadores[inimigo]["vida"] <= 0:
                break

        # Sincroniza variáveis legadas utilizadas no anúncio inicial.
        vida_p1 = jogadores[ctx.author]["vida"]
        vida_p2 = jogadores[inimigo]["vida"]
        mana_p1 = jogadores[ctx.author]["mana"]
        mana_p2 = jogadores[inimigo]["mana"]


def setup_deathbattle_commands(target_bot: commands.Bot) -> None:
    global bot
    bot = target_bot
    target_bot.add_command(deathbattle)
