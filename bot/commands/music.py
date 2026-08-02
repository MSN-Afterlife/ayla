import discord
from discord.ext import commands

from bot.services.music_player import MAX_PLAYLIST_TRACKS
from bot.services.music_player import FILTERS
from bot.services.music_player import MusicError
from bot.services.music_player import MusicService
from bot.services.music_player import RepeatMode
from bot.services.music_player import Track
from bot.services.lyrics_service import LyricsError
from bot.services.lyrics_service import LyricsService


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
    lyrics_service = LyricsService()

    @bot.hybrid_command(name="play", aliases=["p"], description="Toca uma musica, busca ou playlist.")
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
            tracks = await music.resolve_tracks(query, ctx.author.display_name)
            player = music.player_for(ctx.guild.id)
            position = player.add_many(tracks, ctx.channel)
            await player.start_if_idle(voice_client)
        except MusicError as error:
            await ctx.send(str(error))
            return

        if len(tracks) == 1:
            if position > 1:
                await ctx.send(f"Adicionado a fila: **{tracks[0].title}** (`#{position}`)")
            return

        limited = " " if len(tracks) < MAX_PLAYLIST_TRACKS else f" Limitei em {MAX_PLAYLIST_TRACKS} faixas."
        await ctx.send(f"Adicionei **{len(tracks)}** musicas da playlist a fila.{limited}")

    @bot.hybrid_command(name="pause", aliases=["pa"], description="Pausa a musica atual.")
    async def pause(ctx: commands.Context) -> None:
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_playing():
            await _send(ctx, "Nao tem nenhuma musica tocando agora.")
            return

        voice_client.pause()
        await _send(ctx, "Musica pausada.")

    @bot.hybrid_command(name="resume", aliases=["r", "continuar"], description="Continua a musica pausada.")
    async def resume(ctx: commands.Context) -> None:
        voice_client = ctx.voice_client
        if not voice_client or not voice_client.is_paused():
            await _send(ctx, "Nao tem nenhuma musica pausada.")
            return

        voice_client.resume()
        await _send(ctx, "Musica retomada.")

    @bot.hybrid_command(name="skip", aliases=["sk"], description="Pula a musica atual.")
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

    @bot.hybrid_command(name="stop", aliases=["s", "leave", "sair"], description="Para a fila e sai do canal de voz.")
    async def stop(ctx: commands.Context) -> None:
        if not ctx.guild or not ctx.voice_client:
            await _send(ctx, "Nao estou em um canal de voz.")
            return

        await music.player_for(ctx.guild.id).stop(ctx.voice_client)
        await _send(ctx, "Fila encerrada e desconectei do canal de voz.")

    @bot.hybrid_command(name="queue", aliases=["q", "fila"], description="Mostra a fila de musicas.")
    async def queue(ctx: commands.Context) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        player = music.player_for(ctx.guild.id)
        lines = []
        if player.current:
            lines.append(f"Agora: **{player.current.title}** `{_format_duration(player.current_position())}`")

        upcoming = player.queue_snapshot()
        if upcoming:
            lines.extend(_format_queue(upcoming))

        await _send(ctx, "\n".join(lines) if lines else "A fila esta vazia.")

    @bot.hybrid_command(name="nowplaying", aliases=["np"], description="Mostra a musica atual.")
    async def nowplaying(ctx: commands.Context) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        player = music.player_for(ctx.guild.id)
        current = player.current
        if not current:
            await _send(ctx, "Nao tem nenhuma musica tocando agora.")
            return

        duration = _format_track_time(player.current_position(), current.duration)
        await _send(
            ctx,
            f"Tocando agora: **{current.title}**\n"
            f"Tempo: `{duration}` | Volume: `{player.volume_percent}%` | Repeat: `{player.repeat.value}` | Filtro: `{player.filter_name}`\n"
            f"Pedido por: `{current.requested_by}`\n"
            f"{current.webpage_url}",
        )

    @bot.hybrid_command(name="volume", aliases=["v", "vol"], description="Ajusta o volume entre 0 e 200.")
    async def volume(ctx: commands.Context, percent: int | None = None) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        player = music.player_for(ctx.guild.id)
        if percent is None:
            await _send(ctx, f"Volume atual: `{player.volume_percent}%`")
            return

        try:
            value = player.set_volume(percent)
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Volume ajustado para `{value}%`.")

    @bot.hybrid_command(name="repeat", aliases=["rep", "loop"], description="Define repeticao: off, one ou all.")
    async def repeat(ctx: commands.Context, mode: str = "off") -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        normalized = mode.lower()
        if normalized not in {item.value for item in RepeatMode}:
            await _send(ctx, "Use `off`, `one` ou `all`.")
            return

        repeat_mode = music.player_for(ctx.guild.id).set_repeat(RepeatMode(normalized))
        await _send(ctx, f"Repeat definido como `{repeat_mode.value}`.")

    @bot.hybrid_command(name="shuffle", aliases=["sh", "embaralhar"], description="Embaralha a fila.")
    async def shuffle(ctx: commands.Context) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        total = music.player_for(ctx.guild.id).shuffle()
        await _send(ctx, f"Fila embaralhada com `{total}` musica(s).")

    @bot.hybrid_command(name="clear", aliases=["c", "limparfila"], description="Limpa as proximas musicas da fila.")
    async def clear(ctx: commands.Context) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        removed = music.player_for(ctx.guild.id).clear()
        await _send(ctx, f"Removi `{removed}` musica(s) da fila.")

    @bot.hybrid_command(name="remove", aliases=["rm"], description="Remove uma musica da fila pela posicao.")
    async def remove(ctx: commands.Context, position: int) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        try:
            removed = music.player_for(ctx.guild.id).remove(position)
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Removi da fila: **{removed.title}**")

    @bot.hybrid_command(name="move", aliases=["mv"], description="Move uma musica na fila.")
    async def move(ctx: commands.Context, source: int, destination: int) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        try:
            moved = music.player_for(ctx.guild.id).move(source, destination)
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Movi **{moved.title}** para a posicao `{destination}`.")

    @bot.hybrid_command(name="seek", aliases=["go", "avancar"], description="Vai para um tempo da musica atual em segundos.")
    async def seek(ctx: commands.Context, seconds: int) -> None:
        if not ctx.guild or not ctx.voice_client:
            await _send(ctx, "Nao estou tocando nada neste servidor.")
            return

        try:
            await music.player_for(ctx.guild.id).seek(ctx.voice_client, seconds)
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Musica reposicionada para `{_format_duration(seconds)}`.")

    @bot.hybrid_command(name="forward", aliases=["ff"], description="Avanca alguns segundos na musica atual.")
    async def forward(ctx: commands.Context, seconds: int = 10) -> None:
        if not ctx.guild or not ctx.voice_client:
            await _send(ctx, "Nao estou tocando nada neste servidor.")
            return

        try:
            position = await music.player_for(ctx.guild.id).seek_relative(ctx.voice_client, abs(seconds))
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Avancei para `{_format_duration(position)}`.")

    @bot.hybrid_command(name="rewind", aliases=["rw"], description="Volta alguns segundos na musica atual.")
    async def rewind(ctx: commands.Context, seconds: int = 10) -> None:
        if not ctx.guild or not ctx.voice_client:
            await _send(ctx, "Nao estou tocando nada neste servidor.")
            return

        try:
            position = await music.player_for(ctx.guild.id).seek_relative(ctx.voice_client, -abs(seconds))
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Voltei para `{_format_duration(position)}`.")

    @bot.hybrid_command(name="filter", aliases=["flt", "filtro"], description="Aplica filtro: none, bassboost, nightcore, vaporwave ou soft.")
    async def filter_command(ctx: commands.Context, name: str = "none") -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return

        try:
            selected = await music.player_for(ctx.guild.id).set_filter(ctx.voice_client, name)
        except MusicError as error:
            await _send(ctx, str(error))
            return

        await _send(ctx, f"Filtro definido como `{selected}`. Opcoes: `{', '.join(FILTERS)}`.")

    @bot.hybrid_command(name="lyrics", aliases=["ly", "letra"], description="Busca a letra da musica atual ou de uma busca.")
    async def lyrics(ctx: commands.Context, *, query: str | None = None) -> None:
        await ctx.defer()
        try:
            if query:
                result = await lyrics_service.search(query)
            else:
                if not ctx.guild:
                    await ctx.send("Use uma busca ou rode esse comando dentro de um servidor com musica tocando.")
                    return
                current = music.player_for(ctx.guild.id).current
                if not current:
                    await ctx.send("Nao tem musica tocando agora. Use `a!lyrics nome da musica`.")
                    return
                result = await lyrics_service.find_for_track(current)
        except LyricsError as error:
            await ctx.send(str(error))
            return

        pages = _lyrics_pages(result.lyrics)
        await ctx.send(embed=_lyrics_embed(result.title, result.artist, pages, 0), view=LyricsView(result.title, result.artist, pages))


async def _connect_or_move(ctx: commands.Context) -> discord.VoiceClient:
    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    try:
        if not voice_client:
            return await channel.connect()

        if voice_client.channel != channel:
            await voice_client.move_to(channel)
    except RuntimeError as error:
        message = str(error).lower()
        if "davey" in message or "voice" in message:
            raise MusicError("A dependencia de voz esta faltando. Rode `pip install -r requirements.txt` e recrie o container.") from error
        raise

    return voice_client


def _format_queue(tracks: list[Track]) -> list[str]:
    visible_tracks = tracks[:10]
    lines = [f"{index}. **{track.title}** - pedido por `{track.requested_by}`" for index, track in enumerate(visible_tracks, 1)]
    if len(tracks) > len(visible_tracks):
        lines.append(f"...e mais {len(tracks) - len(visible_tracks)} musica(s).")
    return lines


def _format_track_time(position: int, duration: int | None) -> str:
    if duration is None:
        return _format_duration(position)
    return f"{_format_duration(position)} / {_format_duration(duration)}"


def _format_duration(seconds: int) -> str:
    minutes, remaining_seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes}:{remaining_seconds:02d}"


class LyricsView(discord.ui.View):
    def __init__(self, title: str, artist: str | None, pages: list[str]) -> None:
        super().__init__(timeout=180)
        self._title = title
        self._artist = artist
        self._pages = pages
        self._index = 0

    @discord.ui.button(label="Anterior", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._index = (self._index - 1) % len(self._pages)
        await interaction.response.edit_message(embed=_lyrics_embed(self._title, self._artist, self._pages, self._index), view=self)

    @discord.ui.button(label="Proxima", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._index = (self._index + 1) % len(self._pages)
        await interaction.response.edit_message(embed=_lyrics_embed(self._title, self._artist, self._pages, self._index), view=self)


def _lyrics_embed(title: str, artist: str | None, pages: list[str], index: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"Letra - {title}",
        description=pages[index],
        color=0x1DB954,
    )
    if artist:
        embed.add_field(name="Artista", value=artist, inline=False)
    embed.set_footer(text=f"Pagina {index + 1}/{len(pages)}")
    return embed


def _lyrics_pages(lyrics: str) -> list[str]:
    max_length = 3600
    lines = lyrics.splitlines()
    pages = []
    current = ""

    for line in lines:
        candidate = f"{current}\n{line}".strip()
        if len(candidate) > max_length and current:
            pages.append(current)
            current = line
        else:
            current = candidate

    if current:
        pages.append(current)

    return pages or ["Letra indisponivel."]
