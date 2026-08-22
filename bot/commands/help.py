import discord
from discord import app_commands
from discord.ext import commands

from bot.commands.interactions import INTERACTIONS
from bot.config import Settings


HELP_CATEGORIES = {
    "inicio": {
        "label": "Inicio",
        "title": "Central de ajuda",
        "description": (
            "Use os botoes abaixo para navegar pelas categorias.\n"
            "O bot aceita slash commands `/` e o prefixo `{prefix}`."
        ),
        "fields": [
            ("Chat", "Mencione o bot em uma mensagem normal para conversar com a persona.", False),
            ("Canal de chat", "`/chatconfig canal #canal` define um canal fixo para conversa.", False),
            ("Atalho", "`{prefix}interacoes` mostra todas as interacoes sociais.", False),
        ],
        "color": 0x5865F2,
    },
    "geral": {
        "label": "Geral",
        "title": "Comandos gerais",
        "description": "Comandos simples de utilidade.",
        "fields": [
            ("`{prefix}ping`", "Mostra a latencia atual do bot.", True),
            ("`{prefix}hello`", "Recebe uma saudacao do bot.", True),
            ("`{prefix}say <texto>`", "Faz o bot repetir uma mensagem.", True),
            ("`{prefix}calc 10 + 5`", "Calcula soma, subtracao, multiplicacao ou divisao.", False),
            ("`/chatconfig status`", "Mostra onde o chat com a persona esta ativo.", False),
        ],
        "color": 0x3498DB,
    },
    "avisos": {
        "label": "Avisos",
        "title": "Comandos de avisos",
        "description": "Comandos administrativos para anuncios no servidor.",
        "fields": [
            ("`{prefix}avisoayla`", "Envia o aviso oficial de chegada da Ayla no canal atual.", False),
            ("`{prefix}avisoayla #canal`", "Envia o aviso oficial em um canal especifico.", False),
            ("`{prefix}aviso #canal <mensagem>`", "Envia um aviso personalizado em embed.", False),
            ("`{prefix}avisojson #canal` + anexo", "Envia um embed personalizado a partir de um JSON.", False),
            ("`{prefix}avisoimportar nome` + anexo", "Salva um modelo JSON para reutilizar depois.", False),
            ("`{prefix}avisomodelo #canal nome`", "Envia um modelo salvo em `bot/announcements`.", False),
        ],
        "color": 0x9B59B6,
    },
    "midia": {
        "label": "Midia",
        "title": "Comandos de midia",
        "description": "Busca o primeiro resultado encontrado nas fontes configuradas.",
        "fields": [
            ("`{prefix}gif feliz`", "Envia o primeiro GIF encontrado.", False),
            ("`{prefix}imagem gato`", "Envia a primeira imagem encontrada.", False),
            ("`{prefix}youtube musica relaxante`", "Envia o primeiro video encontrado no YouTube.", False),
        ],
        "color": 0xE67E22,
    },
    "musica": {
        "label": "Musica",
        "title": "Comandos de musica",
        "description": "Toca audio em canal de voz usando nome ou link.",
        "fields": [
            ("`{prefix}play` / `{prefix}p`", "Aceita nome, link, playlist, YouTube, SoundCloud e outras fontes do yt-dlp.", False),
            ("Spotify/Deezer", "Links de faixa sao convertidos para busca e tocados por uma fonte compativel.", False),
            ("`{prefix}pa` / `{prefix}r` / `{prefix}sk` / `{prefix}s`", "Pausa, continua, pula ou para a fila.", False),
            ("`{prefix}q` / `{prefix}np`", "Mostra a fila ou a musica atual.", True),
            ("`{prefix}lyrics` / `{prefix}ly` / `{prefix}letra`", "Busca a letra da musica atual ou de uma busca.", False),
            ("`{prefix}v 80` / `{prefix}go 90`", "Ajusta volume ou vai para um tempo da faixa atual.", True),
            ("`{prefix}ff 10` / `{prefix}rw 10`", "Avanca ou volta alguns segundos.", True),
            ("`{prefix}flt bassboost`", "Filtros: none, bassboost, nightcore, vaporwave e soft.", True),
            ("`{prefix}rep one` / `{prefix}sh`", "Controla repeticao e embaralha a fila.", True),
            ("`{prefix}rm 2`, `{prefix}mv 3 1`, `{prefix}c`", "Remove, move ou limpa musicas da fila.", False),
        ],
        "color": 0x1DB954,
    },
    "interacoes": {
        "label": "Interacoes",
        "title": "Interacoes sociais",
        "description": f"Existem {len(INTERACTIONS)} comandos de interacao com embed e GIF.",
        "fields": [
            ("Exemplos", "`{prefix}slap @user`, `{prefix}hug @user`, `{prefix}kiss @user`, `{prefix}pat @user`, `{prefix}bonk @user`", False),
            ("Lista completa", "`{prefix}interacoes`", False),
            ("Sem alvo", "A maioria tambem funciona sem mencionar alguem.", False),
        ],
        "color": 0xFF69B4,
    },
    "ayla": {
        "label": "Ayla",
        "title": "Personalidade da Ayla",
        "description": "A Ayla mantém apenas o contexto recente da conversa atual; não cria perfis afetivos de usuários.",
        "fields": [
            ("Tom", "Ela pode ser casual e brincalhona, mas não deve humilhar, ameaçar ou perseguir pessoas.", False),
            ("Contexto", "O histórico usado no chat é recente e temporário durante o processo do bot.", False),
        ],
        "color": 0x3BA7FF,
    },
    "privacidade": {
        "label": "Privacidade",
        "title": "Privacidade, dados e segurança",
        "description": "Informações importantes sobre o funcionamento da Ayla.",
        "fields": [
            ("Dados usados", "A Ayla processa mensagens necessárias para responder no Discord. O conteúdo pode ser enviado à OpenAI para gerar respostas e ao ClickUp quando alguém autorizado criar um ticket.", False),
            ("Sem perfil pessoal", "A Ayla não deve manter afeto, suspeita, fofoca, preferências ou relações pessoais persistentes sobre usuários.", False),
            ("Exclusão e dúvidas", "Para solicitar exclusão de dados ou denunciar um problema, fale com a equipe responsável pelo servidor.", False),
            ("Políticas do Discord", "[Política de Desenvolvedor](https://support-dev.discord.com/hc/pt-br/articles/8563934450327-Pol%C3%ADtica-de-Desenvolvedor-do-Discord)\n[Termos de Desenvolvedor](https://support-dev.discord.com/hc/pt-br/articles/8562894815383-Termos-de-servi%C3%A7o-de-desenvolvedor-do-Discord)", False),
        ],
        "color": 0x95A5A6,
    },
    "level": {
        "label": "Level",
        "title": "Sistema de level",
        "description": "XP automatico com card separado de progresso e ranking.",
        "fields": [
            ("`{prefix}level` / `{prefix}rank`", "Mostra seu level, rank e barra de XP.", False),
            ("`{prefix}perfil`", "Mostra seu perfil social com sobre mim, economia e background.", False),
            ("`{prefix}perfilsobre <texto>`", "Atualiza o sobre mim do perfil.", False),
            ("`{prefix}perfilbgbuscar <busca>`", "Abre uma escolha de imagens para o background.", False),
            ("`{prefix}rankglobal`", "Mostra o rank global.", False),
            ("`{prefix}top` / `{prefix}ranking`", "Mostra o ranking local.", False),
            ("`{prefix}topglobal`", "Mostra o ranking global.", False),
        ],
        "color": 0xF1C40F,
    },
    "economia": {
        "label": "Economia",
        "title": "Sistema de economia",
        "description": "Winks persistentes com daily, transferencias e apostas.",
        "fields": [
            ("`{prefix}saldo`", "Mostra seu saldo e streak do daily.", False),
            ("`{prefix}daily`", "Abre o link do site para resgatar o daily.", False),
            ("`{prefix}dailyconfig <url>`", "Administradores configuram o link do daily.", False),
            ("`{prefix}pagar @user 100`", "Transfere winks para outra pessoa.", False),
            ("`{prefix}cf 500 cara`", "Aposta cara ou coroa contra a Ayla.", False),
            ("`{prefix}dado 500 6` / `{prefix}slots 500`", "Jogos de cassino com winks.", False),
            ("`{prefix}roleta 500 vermelho`", "Aposta em cor, par/impar ou numero 0-36.", False),
            ("`{prefix}highlow 500 maior`", "Aposta se a proxima carta sera maior ou menor.", False),
            ("`{prefix}21 500`", "Joga 21 contra a Ayla com botoes de pedir/parar.", False),
            ("`{prefix}cartaalta 500`", "Maior carta vence contra a Ayla.", False),
            ("`{prefix}apostar @user 500`", "Desafia outro usuario para uma aposta.", False),
        ],
        "color": 0x2ECC71,
    },
    "uno": {
        "label": "UNO",
        "title": "UNO da Ayla",
        "description": "Mesa de UNO no canal com cartas enviadas por mensagem privada.",
        "fields": [
            ("`{prefix}uno criar` / `{prefix}uno entrar`", "Cria uma mesa e permite 2+ jogadores entrarem.", False),
            ("`{prefix}uno iniciar`", "Inicia a partida e envia a mao de cada jogador no privado.", False),
            ("`{prefix}uno jogar 3 azul`", "Joga a carta numero 3; cor e obrigatoria em coringa e +4.", False),
            ("`{prefix}uno comprar` / `{prefix}uno passar`", "Compra uma carta e passa a vez se nao jogar.", False),
            ("`{prefix}uno mao` / `{prefix}uno mesa` / `{prefix}uno uno`", "Reenvia sua mao, mostra a mesa ou declara UNO.", False),
        ],
        "color": 0xE74C3C,
    },
    "admin": {
        "label": "Admin",
        "title": "Ajuda administrativa",
        "description": "Comandos para moderacao, configuracao e manutencao do bot.",
        "fields": [
            ("`{prefix}statusgeral`", "Mostra diagnostico completo do bot. Aliases: `{prefix}lynstatus`, `{prefix}aylastatus`, `{prefix}botstatus`.", False),
            ("`{prefix}addmoney @user 100`", "Adiciona ou remove winks de uma carteira.", False),
            ("`{prefix}addxp @user 100`", "Adiciona XP local para um usuario.", False),
            ("`{prefix}dailyconfig <url>`", "Configura o link usado pelo comando daily.", False),
            ("`/chatconfig canal #canal`", "Define o canal fixo onde a Ayla conversa.", False),
            ("`/chatconfig limpar`", "Remove o canal fixo de conversa.", False),
            ("`{prefix}aviso #canal <mensagem>`", "Envia um aviso simples em embed.", False),
            ("`{prefix}avisoayla #canal`", "Envia o aviso oficial de chegada da Ayla.", False),
            ("`{prefix}avisojson #canal` + anexo", "Envia um embed a partir de JSON anexado.", False),
            ("`{prefix}avisoimportar nome` + anexo", "Salva um modelo JSON em `bot/announcements`.", False),
            ("`{prefix}avisomodelo #canal nome`", "Envia um modelo de aviso salvo.", False),
        ],
        "color": 0xE67E22,
    },
}


def setup_help_command(bot: commands.Bot, settings: Settings) -> None:
    @bot.command(name="help", aliases=["ajuda", "comandos", "menu"])
    async def help_command(ctx: commands.Context) -> None:
        view = HelpView(settings.command_prefix)
        await ctx.send(embed=build_help_embed("inicio", settings.command_prefix), view=view)

    @bot.command(name="helpadmin", aliases=["adminhelp", "ajudaadmin", "comandosadmin"])
    @commands.has_permissions(manage_guild=True)
    async def admin_help_command(ctx: commands.Context) -> None:
        await ctx.send(embed=build_help_embed("admin", settings.command_prefix))

    @bot.tree.command(name="help", description="Abre o menu de ajuda do bot.")
    async def help_slash(interaction: discord.Interaction) -> None:
        view = HelpView("/")
        try:
            await interaction.response.defer()
            await interaction.followup.send(embed=build_help_embed("inicio", "/"), view=view)
        except discord.NotFound:
            if interaction.channel:
                await interaction.channel.send(embed=build_help_embed("inicio", "/"), view=view)

    @bot.tree.command(name="helpadmin", description="Abre a ajuda administrativa do bot.")
    @app_commands.default_permissions(manage_guild=True)
    async def admin_help_slash(interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(embed=build_help_embed("admin", "/"), ephemeral=True)
        except discord.NotFound:
            if interaction.channel:
                await interaction.channel.send(embed=build_help_embed("admin", "/"))


class HelpView(discord.ui.View):
    def __init__(self, prefix: str) -> None:
        super().__init__(timeout=180)
        self._prefix = prefix

        for category_id, category in HELP_CATEGORIES.items():
            self.add_item(HelpButton(category_id, category["label"], prefix))


class HelpButton(discord.ui.Button):
    def __init__(self, category_id: str, label: str, prefix: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary if category_id == "inicio" else discord.ButtonStyle.secondary)
        self._category_id = category_id
        self._prefix = prefix

    async def callback(self, interaction: discord.Interaction) -> None:
        view = HelpView(self._prefix)
        try:
            await interaction.response.defer()
            await interaction.edit_original_response(embed=build_help_embed(self._category_id, self._prefix), view=view)
        except discord.NotFound:
            if interaction.channel:
                await interaction.channel.send(embed=build_help_embed(self._category_id, self._prefix), view=view)


def build_help_embed(category_id: str, prefix: str) -> discord.Embed:
    category = HELP_CATEGORIES[category_id]
    description = category["description"].replace("{prefix}", prefix)
    embed = discord.Embed(
        title=category["title"],
        description=description,
        color=category["color"],
    )

    for name, value, inline in category["fields"]:
        embed.add_field(name=name.replace("{prefix}", prefix), value=value.replace("{prefix}", prefix), inline=inline)

    embed.set_footer(text="Use os botoes para trocar de categoria.")
    return embed
