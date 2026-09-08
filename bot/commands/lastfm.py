import time
from html import escape

import discord
from discord.ext import commands

from bot.services.lastfm_service import LastFmError, LastFmService


def setup_lastfm_commands(bot: commands.Bot, service: LastFmService) -> None:
    @bot.hybrid_group(name="lastfm", description="Vincula e administra seu Last.fm.")
    async def lastfm(ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/lastfm conectar`, `status`, `ativar`, `desativar`, `desconectar`, `agora`, `recentes` ou `diagnostico`.", ephemeral=bool(ctx.interaction))

    def unavailable(ctx):
        return not service.available

    @lastfm.command(name="conectar", description="Gera um link seguro para vincular o Last.fm.")
    async def connect(ctx: commands.Context) -> None:
        if unavailable(ctx):
            await ctx.send("A integração Last.fm está indisponível: configure as variáveis necessárias.", ephemeral=True); return
        state = service.repository.create_state(ctx.author.id)
        await ctx.send(f"Autorize sua conta Last.fm neste link (expira em 15 minutos):\n{service.authorization_url(state)}", ephemeral=True)

    @lastfm.command(name="status", description="Mostra o status do seu vínculo Last.fm.")
    async def status(ctx: commands.Context) -> None:
        account = service.repository.get_account(ctx.author.id)
        if unavailable(ctx): await ctx.send("A integração Last.fm está indisponível.", ephemeral=True); return
        if not account: await ctx.send("Nenhuma conta Last.fm vinculada.", ephemeral=True); return
        await ctx.send(f"Conta: **{escape(account.username)}**\nScrobble: **{'ativado' if account.scrobble_enabled else 'desativado'}**\nModo: **{account.scrobble_mode}**", ephemeral=True)

    @lastfm.command(name="ativar", description="Ativa o scrobble.")
    async def enable(ctx: commands.Context) -> None:
        if service.repository.get_account(ctx.author.id) is None: await ctx.send("Vincule uma conta primeiro com `/lastfm conectar`.", ephemeral=True); return
        service.repository.set_enabled(ctx.author.id, True); await ctx.send("Scrobble ativado.", ephemeral=True)

    @lastfm.command(name="desativar", description="Desativa o scrobble sem remover o vínculo.")
    async def disable(ctx: commands.Context) -> None:
        if not service.repository.set_enabled(ctx.author.id, False): await ctx.send("Nenhuma conta vinculada.", ephemeral=True); return
        await ctx.send("Scrobble desativado. O vínculo foi preservado.", ephemeral=True)

    @lastfm.command(name="desconectar", description="Remove o vínculo Last.fm.")
    async def disconnect(ctx: commands.Context, confirmar: bool = False) -> None:
        if not confirmar:
            await ctx.send("Isso remove o vínculo local. Execute `/lastfm desconectar confirmar:true` para confirmar.", ephemeral=True); return
        service.repository.disconnect(ctx.author.id); await ctx.send("Conta Last.fm desconectada localmente. Se desejar, revogue também o acesso nas configurações do Last.fm.", ephemeral=True)

    @lastfm.command(name="agora", description="Mostra sua faixa mais recente.")
    async def now(ctx: commands.Context) -> None:
        account = service.repository.get_account(ctx.author.id)
        if not account: await ctx.send("Nenhuma conta vinculada.", ephemeral=True); return
        try: item = await service.now(account)
        except LastFmError as error: await ctx.send(f"Não foi possível consultar o Last.fm: {error}", ephemeral=True); return
        if not item: await ctx.send("Não há histórico recente no Last.fm.", ephemeral=True); return
        await ctx.send(_format_item(item), ephemeral=True)

    @lastfm.command(name="recentes", description="Mostra suas últimas faixas.")
    async def recent(ctx: commands.Context) -> None:
        account = service.repository.get_account(ctx.author.id)
        if not account: await ctx.send("Nenhuma conta vinculada.", ephemeral=True); return
        try: items = await service.recent(account)
        except LastFmError as error: await ctx.send(f"Não foi possível consultar o Last.fm: {error}", ephemeral=True); return
        await ctx.send("\n".join(f"{i}. {_format_item(item)}" for i, item in enumerate(items, 1))[:1900] or "Não há histórico recente.", ephemeral=True)

    @lastfm.command(name="diagnostico", description="Mostra o resultado do último scrobble de um usuário.")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def diagnostic(ctx: commands.Context, usuario: discord.Member | None = None) -> None:
        target = usuario or ctx.author
        result = getattr(bot, "_lastfm_scrobbler").diagnostic(ctx.guild.id, target.id)
        if not result:
            await ctx.send(f"Não encontrei uma faixa recente registrada para {target.mention}.", ephemeral=True)
            return
        status = result["result"]
        played = f'{result["played"]}s/{result["threshold"]:g}s'
        await ctx.send(
            f"**Diagnóstico Last.fm — {target.display_name}**\n"
            f"Faixa: **{escape(result['artist'])} — {escape(result['track'])}**\n"
            f"Reprodução: `{played}`\nResultado: **{escape(status)}**\n"
            f"ID: `{result['playback_id']}`",
            ephemeral=True,
        )


def _format_item(item: dict) -> str:
    artist = escape(str((item.get("artist") or {}).get("#text") or "Artista"))
    title = escape(str(item.get("name") or "Faixa"))
    return f"**{artist} — {title}**"
