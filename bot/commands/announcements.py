import json
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands
from discord.utils import utcnow


ANNOUNCEMENT_DIR = Path("bot/announcements")


def setup_announcement_commands(bot: commands.Bot) -> None:
    @bot.hybrid_command(name="avisoayla", aliases=["anunciarayla"], description="Envia o aviso oficial de chegada da Ayla.")
    @commands.has_permissions(manage_guild=True)
    async def announce_ayla(ctx: commands.Context, channel: discord.TextChannel | None = None) -> None:
        if not ctx.guild:
            await ctx.send("Esse comando funciona dentro de um servidor.")
            return

        target_channel = channel or ctx.channel
        embed = _ayla_announcement_embed(ctx.guild, bot.user)
        await target_channel.send(embed=embed)
        await _confirm(ctx, target_channel)

    @bot.hybrid_command(name="aviso", aliases=["announce"], description="Envia um aviso em embed.")
    @commands.has_permissions(manage_guild=True)
    async def announce(ctx: commands.Context, channel: discord.TextChannel, *, message: str) -> None:
        embed = discord.Embed(
            title="Aviso",
            description=message,
            color=0x5865F2,
            timestamp=utcnow(),
        )
        if ctx.guild and ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.replace(format="png", size=128).url)
        embed.set_footer(text=f"Enviado por {ctx.author.display_name}")

        await channel.send(embed=embed)
        await _confirm(ctx, channel)

    @bot.hybrid_command(name="avisojson", aliases=["embedjson"], description="Envia um aviso a partir de JSON anexado.")
    @commands.has_permissions(manage_guild=True)
    async def announce_json(
        ctx: commands.Context,
        channel: discord.TextChannel,
        attachment: discord.Attachment | None = None,
    ) -> None:
        attachment = attachment or (ctx.message.attachments[0] if ctx.message and ctx.message.attachments else None)
        if not attachment:
            await ctx.send("Anexe um arquivo `.json` com o embed.")
            return

        try:
            payload = json.loads((await attachment.read()).decode("utf-8-sig"))
            content, embed = _message_from_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            await ctx.send(f"JSON invalido: {error}")
            return

        await channel.send(content=content, embed=embed)
        await _confirm(ctx, channel)

    @bot.hybrid_command(name="avisomodelo", aliases=["avisofile"], description="Envia um aviso salvo em bot/announcements.")
    @commands.has_permissions(manage_guild=True)
    async def announce_file(ctx: commands.Context, channel: discord.TextChannel, filename: str) -> None:
        try:
            path = _announcement_path(filename)
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            content, embed = _message_from_payload(payload)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            await ctx.send(f"Nao consegui carregar esse aviso: {error}")
            return

        await channel.send(content=content, embed=embed)
        await _confirm(ctx, channel)

    @bot.hybrid_command(name="avisoimportar", aliases=["salvaraviso"], description="Salva um modelo de aviso JSON.")
    @commands.has_permissions(manage_guild=True)
    async def import_announcement(ctx: commands.Context, name: str, attachment: discord.Attachment | None = None) -> None:
        attachment = attachment or (ctx.message.attachments[0] if ctx.message and ctx.message.attachments else None)
        if not attachment:
            await ctx.send("Anexe um arquivo `.json` para salvar como modelo.")
            return

        safe_name = _safe_filename(name)
        if not safe_name:
            await ctx.send("Use um nome simples para o modelo, sem barras ou caracteres especiais.")
            return

        try:
            raw = (await attachment.read()).decode("utf-8-sig")
            payload = json.loads(raw)
            _message_from_payload(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            await ctx.send(f"JSON invalido: {error}")
            return

        ANNOUNCEMENT_DIR.mkdir(parents=True, exist_ok=True)
        path = ANNOUNCEMENT_DIR / f"{safe_name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        await ctx.send(f"Modelo `{safe_name}` salvo. Use `a!avisomodelo #canal {safe_name}`.")


async def _confirm(ctx: commands.Context, channel: discord.TextChannel) -> None:
    if ctx.channel and channel.id == ctx.channel.id:
        return
    await ctx.send(f"Aviso enviado em {channel.mention}.")


def _message_from_payload(payload: dict[str, Any]) -> tuple[str | None, discord.Embed]:
    if not isinstance(payload, dict):
        raise ValueError("a raiz precisa ser um objeto JSON")

    embed_payload = payload.get("embed", payload)
    if not isinstance(embed_payload, dict):
        raise ValueError("o campo embed precisa ser um objeto")

    color = _parse_color(embed_payload.get("color", 0x5865F2))
    embed = discord.Embed(
        title=_optional_str(embed_payload.get("title")),
        description=_optional_str(embed_payload.get("description")),
        url=_optional_str(embed_payload.get("url")),
        color=color,
        timestamp=utcnow() if embed_payload.get("timestamp", True) else None,
    )

    author = embed_payload.get("author")
    if isinstance(author, dict) and author.get("name"):
        embed.set_author(name=str(author["name"]), icon_url=_optional_str(author.get("icon_url")), url=_optional_str(author.get("url")))

    thumbnail = embed_payload.get("thumbnail")
    if isinstance(thumbnail, str):
        embed.set_thumbnail(url=thumbnail)
    elif isinstance(thumbnail, dict) and thumbnail.get("url"):
        embed.set_thumbnail(url=str(thumbnail["url"]))

    image = embed_payload.get("image")
    if isinstance(image, str):
        embed.set_image(url=image)
    elif isinstance(image, dict) and image.get("url"):
        embed.set_image(url=str(image["url"]))

    footer = embed_payload.get("footer")
    if isinstance(footer, str):
        embed.set_footer(text=footer)
    elif isinstance(footer, dict) and footer.get("text"):
        embed.set_footer(text=str(footer["text"]), icon_url=_optional_str(footer.get("icon_url")))

    for field in embed_payload.get("fields", []):
        if not isinstance(field, dict) or not field.get("name") or not field.get("value"):
            continue
        embed.add_field(name=str(field["name"]), value=str(field["value"]), inline=bool(field.get("inline", False)))

    content = _optional_str(payload.get("content"))
    return content, embed


def _ayla_announcement_embed(guild: discord.Guild, bot_user: discord.ClientUser | discord.User | None) -> discord.Embed:
    embed = discord.Embed(
        title="Ayla chegou ao servidor",
        description=(
            "A partir de agora, a Ayla faz parte oficialmente da nossa comunidade como bot oficial do servidor.\n\n"
            "Ela vai ajudar com comandos, interacoes, sistema de level, perfil, economia, musica e outras novidades que vamos liberar aos poucos."
        ),
        color=0x9B59B6,
        timestamp=utcnow(),
    )
    embed.add_field(
        name="O que ela ja faz",
        value=(
            "- Conversa com a comunidade\n"
            "- Mostra perfil, level e ranking\n"
            "- Gerencia economia e daily pelo site\n"
            "- Toca musicas e busca letras\n"
            "- Envia imagens, GIFs e interacoes sociais"
        ),
        inline=False,
    )
    embed.add_field(name="Como usar", value="Use `a!help` ou `/help` para abrir o menu de comandos.", inline=False)
    embed.add_field(name="Boas-vindas", value="Recebam bem a Ayla. Ela vai crescer junto com o MSN Afterlife.", inline=False)

    if bot_user:
        embed.set_thumbnail(url=bot_user.display_avatar.replace(format="png", size=256).url)
        embed.set_footer(text=f"{bot_user.display_name} | Bot oficial")
    else:
        embed.set_footer(text="Ayla | Bot oficial")

    if guild.icon:
        embed.set_author(name=guild.name, icon_url=guild.icon.replace(format="png", size=128).url)
    else:
        embed.set_author(name=guild.name)

    return embed


def _announcement_path(filename: str) -> Path:
    safe_name = _safe_filename(filename)
    if not safe_name:
        raise ValueError("nome de modelo invalido")
    path = (ANNOUNCEMENT_DIR / f"{safe_name}.json").resolve()
    root = ANNOUNCEMENT_DIR.resolve()
    if root not in path.parents:
        raise ValueError("modelo fora da pasta permitida")
    return path


def _safe_filename(value: str) -> str:
    name = Path(value).stem.lower()
    return "".join(char for char in name if char.isalnum() or char in {"-", "_"})


def _parse_color(value: Any) -> int:
    if isinstance(value, int):
        return max(0, min(value, 0xFFFFFF))
    if isinstance(value, str):
        normalized = value.strip().removeprefix("#").removeprefix("0x")
        return int(normalized, 16)
    return 0x5865F2


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
