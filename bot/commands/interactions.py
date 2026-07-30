from dataclasses import dataclass

import discord
from discord.ext import commands

from bot.config import Settings
from bot.services.media_search import MediaSearch
from bot.services.media_search import MediaSearchError


@dataclass(frozen=True)
class InteractionAction:
    name: str
    aliases: list[str]
    verb: str
    self_text: str
    gif_query: str
    color: int


INTERACTIONS: list[InteractionAction] = [
    InteractionAction("slap", ["tapa", "tapear"], "deu um tapa em", "deu um tapa dramatico no ar", "anime slap", 0xE74C3C),
    InteractionAction("hug", ["abraco", "huggar"], "abracou", "se abracou para recarregar as energias", "anime hug", 0xF78FB3),
    InteractionAction("kiss", ["beijo", "beijar"], "beijou", "mandou um beijo para o vazio", "anime kiss", 0xFF6B9A),
    InteractionAction("pat", ["cafune", "carinho"], "fez carinho em", "fez carinho na propria cabeca", "anime head pat", 0xF8C471),
    InteractionAction("poke", ["cutucar"], "cutucou", "cutucou o proprio ombro", "anime poke", 0x85C1E9),
    InteractionAction("bonk", ["marretar"], "deu um bonk em", "tentou dar um bonk no proprio pensamento", "anime bonk", 0xD35400),
    InteractionAction("punch", ["soco", "socar"], "deu um soco em", "soco no ar, treino puro", "anime punch", 0xC0392B),
    InteractionAction("kick", ["chute", "chutar"], "chutou", "chutou o vento com estilo", "anime kick", 0xA93226),
    InteractionAction("bite", ["morder", "mordida"], "mordeu", "mordeu a propria paciencia", "anime bite", 0x884EA0),
    InteractionAction("lick", ["lamber"], "lambeu", "lambeu o nada e fingiu normalidade", "anime lick", 0xBB8FCE),
    InteractionAction("cuddle", ["aconchegar"], "se aconchegou em", "se enrolou em conforto imaginario", "anime cuddle", 0xF1948A),
    InteractionAction("tickle", ["cosquinha"], "fez cosquinha em", "riu sozinho com cosquinha imaginaria", "anime tickle", 0xF7DC6F),
    InteractionAction("highfive", ["hifive", "tocaai"], "bateu aqui com", "levantou a mao esperando um high five", "anime high five", 0x58D68D),
    InteractionAction("wave", ["acenar"], "acenou para", "acenou para todo mundo", "anime wave", 0x5DADE2),
    InteractionAction("wink", ["piscadinha"], "piscou para", "piscou para a camera invisivel", "anime wink", 0xAF7AC5),
    InteractionAction("smile", ["sorrir"], "sorriu para", "sorriu como quem sabe de algo", "anime smile", 0xF4D03F),
    InteractionAction("cry", ["chorar"], "chorou com", "chorou em modo cinematografico", "anime cry", 0x5499C7),
    InteractionAction("laugh", ["rir"], "riu com", "riu sozinho e assumiu o momento", "anime laugh", 0xF5B041),
    InteractionAction("dance", ["dancar"], "dancou com", "dancou sem precisar de plateia", "anime dance", 0x45B39D),
    InteractionAction("blush", ["corar"], "ficou sem jeito com", "ficou vermelho do nada", "anime blush", 0xEC7063),
    InteractionAction("stare", ["encarar"], "encarou", "encarou o horizonte", "anime stare", 0x566573),
    InteractionAction("shrug", ["tanto-faz"], "deu de ombros para", "deu de ombros para a situacao", "anime shrug", 0x85929E),
    InteractionAction("facepalm", ["maonacara"], "sentiu vergonha alheia com", "fez facepalm sozinho", "anime facepalm", 0x7F8C8D),
    InteractionAction("handhold", ["maos", "segurarmao"], "segurou a mao de", "segurou a propria mao para dar coragem", "anime hand holding", 0xFAD7A0),
    InteractionAction("boop", ["boopar"], "deu um boop em", "boopou o ar", "anime boop nose", 0xAED6F1),
    InteractionAction("yeet", ["arremessar"], "arremessou", "arremessou as preocupacoes para longe", "anime yeet", 0xE67E22),
    InteractionAction("throw", ["jogar"], "jogou algo em", "jogou uma ideia para o alto", "anime throw", 0xD68910),
    InteractionAction("feed", ["alimentar"], "deu comida para", "preparou um lanchinho moral", "anime feed", 0x52BE80),
    InteractionAction("tea", ["cha"], "tomou cha com", "tomou um cha para acalmar", "anime tea", 0x7DCEA0),
    InteractionAction("coffee", ["cafe"], "tomou cafe com", "tomou cafe encarando a vida", "anime coffee", 0x6E2C00),
    InteractionAction("clap", ["aplaudir"], "aplaudiu", "aplaudiu uma cena imaginaria", "anime clap", 0xF9E79F),
    InteractionAction("cheer", ["torcer"], "torceu por", "torceu por si mesmo", "anime cheer", 0x2ECC71),
    InteractionAction("comfort", ["consolar"], "consolou", "tentou se consolar com dignidade", "anime comfort", 0x76D7C4),
    InteractionAction("protect", ["proteger"], "protegeu", "ativou modo protecao", "anime protect", 0x3498DB),
    InteractionAction("run", ["correr"], "correu com", "correu dos problemas por esporte", "anime run", 0x1ABC9C),
    InteractionAction("sleep", ["dormir"], "tirou uma soneca com", "entrou em modo soneca", "anime sleep", 0x5D6D7E),
    InteractionAction("wake", ["acordar"], "acordou", "tentou acordar a propria alma", "anime wake up", 0xF39C12),
    InteractionAction("scare", ["assustar"], "assustou", "assustou o silencio", "anime scare", 0x8E44AD),
    InteractionAction("confused", ["confuso"], "ficou confuso com", "ficou confuso em tempo integral", "anime confused", 0x95A5A6),
    InteractionAction("happy", ["feliz"], "ficou feliz com", "ficou feliz do nada", "anime happy", 0xF1C40F),
    InteractionAction("sad", ["triste"], "ficou triste com", "ficou triste olhando para o nada", "anime sad", 0x2874A6),
    InteractionAction("angry", ["bravo"], "ficou bravo com", "ficou bravo em silencio", "anime angry", 0xCB4335),
    InteractionAction("smug", ["convencido"], "fez cara convencida para", "ficou convencido sem motivo claro", "anime smug", 0xAF601A),
    InteractionAction("nod", ["concordar"], "concordou com", "concordou consigo mesmo", "anime nod", 0x27AE60),
    InteractionAction("nope", ["negar"], "negou para", "negou com energia", "anime nope", 0x922B21),
    InteractionAction("pray", ["rezar"], "rezou por", "rezou pela situacao", "anime pray", 0xD7BDE2),
    InteractionAction("bow", ["curvar"], "se curvou para", "se curvou com respeito", "anime bow", 0xA569BD),
    InteractionAction("salute", ["saudar"], "saudou", "prestou continencia ao momento", "anime salute", 0x5499C7),
    InteractionAction("invite", ["convidar"], "convidou", "fez um convite misterioso", "anime invite", 0x48C9B0),
    InteractionAction("gift", ["presente"], "deu um presente para", "guardou um presente imaginario", "anime gift", 0xE84393),
    InteractionAction("flower", ["flor"], "entregou flores para", "segurou flores dramaticamente", "anime flowers", 0xF5B7B1),
    InteractionAction("cookie", ["biscoito"], "deu um biscoito para", "comeu um biscoito emocional", "anime cookie", 0xCA6F1E),
    InteractionAction("steal", ["roubar"], "roubou algo de", "roubou a cena por um segundo", "anime steal", 0x616A6B),
    InteractionAction("sip", ["beber"], "bebeu algo com", "tomou um gole observador", "anime sip", 0x7E5109),
    InteractionAction("spin", ["girar"], "girou com", "girou sem explicar nada", "anime spin", 0x85C1E9),
    InteractionAction("jump", ["pular"], "pulou com", "pulou de animacao", "anime jump", 0x58D68D),
    InteractionAction("celebrate", ["celebrar"], "celebrou com", "celebrou uma pequena vitoria", "anime celebrate", 0xF7DC6F),
    InteractionAction("cringe", ["vergonha"], "sentiu cringe com", "sentiu cringe do proprio passado", "anime cringe", 0x99A3A4),
    InteractionAction("glare", ["fuzilar"], "olhou torto para", "olhou torto para o universo", "anime glare", 0x34495E),
    InteractionAction("thank", ["agradecer"], "agradeceu", "agradeceu baixinho", "anime thank you", 0x82E0AA),
    InteractionAction("sorry", ["desculpar"], "pediu desculpas para", "pediu desculpas para a existencia", "anime sorry", 0xAED6F1),
    InteractionAction("love", ["amar"], "demonstrou amor por", "espalhou amor pelo chat", "anime love", 0xFF69B4),
    InteractionAction("hate", ["odiar"], "declarou rivalidade com", "brigou com a propria paciencia", "anime hate angry", 0x943126),
    InteractionAction("respect", ["respeitar"], "demonstrou respeito por", "mostrou respeito ao momento", "anime respect", 0x2E86C1),
]


def setup_interaction_commands(bot: commands.Bot, settings: Settings) -> None:
    media_search = MediaSearch(settings)

    for action in INTERACTIONS:
        bot.add_command(_build_interaction_command(action, media_search))

    @bot.command(name="interacoes", aliases=["interactions", "acoes"])
    async def interactions(ctx: commands.Context) -> None:
        names = ", ".join(f"`+{action.name}`" for action in INTERACTIONS)
        embed = discord.Embed(
            title=f"{len(INTERACTIONS)} comandos de interacao",
            description=names,
            color=0x5865F2,
        )
        embed.set_footer(text="Use: +slap @usuario ou !slap @usuario")
        await ctx.send(embed=embed)


def _build_interaction_command(action: InteractionAction, media_search: MediaSearch) -> commands.Command:
    async def callback(ctx: commands.Context, target: discord.Member | None = None) -> None:
        author = ctx.author
        target_text = target.mention if target else None
        description = (
            f"{author.mention} {action.verb} {target_text}!"
            if target_text
            else f"{author.mention} {action.self_text}!"
        )

        embed = discord.Embed(
            title=action.name.capitalize(),
            description=description,
            color=action.color,
        )
        embed.set_footer(text=f"Pedido por {author.display_name}")

        try:
            gif_url = await media_search.search_gif(action.gif_query)
            embed.set_image(url=gif_url)
        except MediaSearchError as error:
            embed.add_field(name="GIF indisponivel", value=str(error), inline=False)

        await ctx.send(embed=embed)

    return commands.Command(callback, name=action.name, aliases=action.aliases)
