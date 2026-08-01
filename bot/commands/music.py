import discord
from discord.ext import commands

from bot.services.music_player import MusicError
from bot.services.music_player import MusicService
from bot.services.music_player import Track


async def _send(ctx: commands.Context, message: str) -> None:
    try:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        await ctx.send(message)
    except discord.NotFound:
        if ctx.channel:
            await ctx.channel.send(message)


def setup_music_commands(bot: commands.Bot) -> None:
    music = MusicService(bot)

    @bot.hybrid_command(name="play", aliases=["p"], description="Toca uma musica por nome ou link.")
    async def play(ctx: commands.Context, *, query: str) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return
        if not ctx.author.voice or not ctx.author.voice.channel:
            await _send(ctx, "Entre em um canal de voz primeiro.")
            return

        await ctx.defer()
        try:
            voice_client = await _connect_or_move(ctx)
            track = await music.resolve_track(query, ctx.author.display_name)
            player = music.player_for(ctx.guild.id)
            position = await player.add(track, ctx.channel)
            await player.start_if_idle(voice_client)
        except MusicError as error:
            await ctx.send(str(error))
            return

        if position > 1:
            await ctx.send(f"Adicionado a fila: **{track.title}** (`#{position}`)")

    @bot.hybrid_command(name="pause", description="Pausa a musica atual.")
    async def pause(ctx: commands.Context) -> None:
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_playing():
            await _send(ctx, "Nao tem nenhuma musica tocando agora.")
            return

        voice_client.pause()
        await _send(ctx, "Musica pausada.")

    @bot.hybrid_command(name="resume", aliases=["continuar"], description="Continua a musica pausada.")
    async def resume(ctx: commands.Context) -> None:
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_paused():
            await _send(ctx, "Nao tem nenhuma musica pausada.")
            return

        voice_client.resume()
        await _send(ctx, "Musica retomada.")

    @bot.hybrid_command(name="skip", aliases=["pular"], description="Pula a musica atual.")
    async def skip(ctx: commands.Context) -> None:
        if not ctx.guild or not ctx.voice_client:
            await _send(ctx, "Nao estou tocando nada neste servidor.")
            return

        try:
            await music.player_for(ctx.guild.id).skip(ctx.voice_client)
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, "Musica pulada.")

    @bot.hybrid_command(name="stop", aliases=["leave", "sair"], description="Para a fila e sai do canal de voz.")
    async def stop(ctx: commands.Context) -> None:
        if not ctx.guild or not ctx.voice_client:
            await _send(ctx, "Nao estou em um canal de voz.")
            return

        await music.player_for(ctx.guild.id).stop(ctx.voice_client)
        await _send(ctx, "Fila encerrada e desconectei do canal de voz.")

    @bot.hybrid_command(name="queue", aliases=["fila"], description="Mostra a fila de musicas.")
    async def queue(ctx: commands.Context) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        player = music.player_for(ctx.guild.id)
        lines = []
        if player.current:
            lines.append(f"Agora: **{player.current.title}**")

        upcoming = player.queue_snapshot()
        if upcoming:
            lines.extend(_format_queue(upcoming))

        await _send(ctx, "\n".join(lines) if lines else "A fila esta vazia.")

    @bot.hybrid_command(name="nowplaying", aliases=["np"], description="Mostra a musica atual.")
    async def nowplaying(ctx: commands.Context) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        current = music.player_for(ctx.guild.id).current
        if not current:
            await _send(ctx, "Nao tem nenhuma musica tocando agora.")
            return

        await _send(ctx, f"Tocando agora: **{current.title}**\nPedido por: `{current.requested_by}`\n{current.webpage_url}")


async def _connect_or_move(ctx: commands.Context) -> discord.VoiceClient:
    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    if not voice_client:
        return await channel.connect()

    if voice_client.channel != channel:
        await voice_client.move_to(channel)

    return voice_client


def _format_queue(tracks: list[Track]) -> list[str]:
    visible_tracks = tracks[:10]
    lines = [f"{index}. **{track.title}** - pedido por `{track.requested_by}`" for index, track in enumerate(visible_tracks, 1)]
    if len(tracks) > len(visible_tracks):
        lines.append(f"...e mais {len(tracks) - len(visible_tracks)} musica(s).")
    return lines
