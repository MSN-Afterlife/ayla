import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.services.music_player import MAX_PLAYLIST_TRACKS
from bot.services.music_player import FILTERS
from bot.services.music_player import MusicError
from bot.services.music_player import MusicService
from bot.services.music_player import RepeatMode
from bot.services.music_player import Track
from bot.services.music_player import build_now_playing_embed
from bot.services.lyrics_service import LyricsError
from bot.services.lyrics_service import LyricsService


logger = logging.getLogger(__name__)
REPEAT_CHOICES = [
    app_commands.Choice(name="Desligado", value="off"),
    app_commands.Choice(name="Uma musica", value="one"),
    app_commands.Choice(name="Fila inteira", value="all"),
]
FILTER_CHOICES = [app_commands.Choice(name=name, value=name) for name in FILTERS]


async def _send(ctx: commands.Context, message: str) -> None:
    try:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer()
        await ctx.send(message)
    except discord.NotFound:
        if ctx.channel:
            await ctx.channel.send(message)


def setup_music_commands(bot: commands.Bot, settings: Settings) -> None:
    music = MusicService(bot, settings)
    bot._music_service = music
    cookies_valid, cookies_message = music.validate_youtube_cookies()
    print(f"[MUSIC] Cookies do YouTube: {'OK' if cookies_valid else 'AVISO'} - {cookies_message}")
    lyrics_service = LyricsService()

    @bot.hybrid_command(name="play", aliases=["p"], description="Toca uma musica, busca ou playlist.")
    async def play(ctx: commands.Context, *, query: str) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return
        if not ctx.author.voice or not ctx.author.voice.channel:
            await _send(ctx, "Entre em um canal de voz primeiro.")
            return
        music.player_for(ctx.guild.id).cancel_autoplay()

        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        else:
            await ctx.defer()
        try:
            tracks = await music.resolve_tracks(query, ctx.author.display_name, ctx.author.id)
            player = music.player_for(ctx.guild.id)
            async with music.voice_lock_for(ctx.guild.id):
                async with player._advance_lock:
                    # Nunca troque o backend da faixa atual apenas porque uma nova
                    # entrada foi enfileirada. A troca ocorre em _play_next_impl,
                    # quando a faixa corrente termina ou e pulada.
                    use_lavalink = player.current.provider == "lavalink" if player.current else tracks[0].provider == "lavalink"
                    voice_client = await _connect_or_move(ctx, use_lavalink=use_lavalink)
            player.set_now_playing_view_factory(lambda player: MusicNowPlayingView(music, lyrics_service, player))
            position = player.add_many(tracks, ctx.channel)
            await player.start_if_idle(voice_client)
        except MusicError as error:
            logger.warning("Falha ao executar play para %s: %s", ctx.author, error)
            await _send(ctx, "Deu erro ao tentar tocar essa musica. Tente outro link ou outra busca.")
            return
        except Exception:
            logger.exception("Erro inesperado ao executar play para %s", ctx.author)
            await _send(ctx, "Deu erro ao tentar tocar essa musica. Tente novamente mais tarde.")
            return

        if len(tracks) == 1:
            if position > 1:
                await ctx.send(embed=_queued_embed(tracks[0], position))
            elif ctx.interaction:
                await ctx.send("Musica iniciada.", ephemeral=True)
            return

        limited = " " if len(tracks) < MAX_PLAYLIST_TRACKS else f" Limitei em {MAX_PLAYLIST_TRACKS} faixas."
        await ctx.send(f"Adicionei **{len(tracks)}** musicas da playlist a fila a partir da posicao `#{position}`.{limited}")

    @bot.hybrid_command(
        name="musiccheck",
        aliases=["ytcheck", "checkcookies"],
        description="Verifica os cookies do YouTube (administradores).",
    )
    async def musiccheck(ctx: commands.Context, *, url: str | None = None) -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return
        permissions = getattr(ctx.author, "guild_permissions", None)
        if not permissions or not permissions.manage_guild:
            await _send(ctx, "Apenas administradores podem usar esse comando.")
            return

        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=True)

        if not url:
            valid, message = music.validate_youtube_cookies()
            prefix = "OK" if valid else "ERRO"
            await ctx.send(f"`{prefix}` {message}", ephemeral=bool(ctx.interaction))
            return

        valid, message = await music.test_youtube_cookies(url)
        prefix = "OK" if valid else "ERRO"
        await ctx.send(f"`{prefix}` {message}", ephemeral=bool(ctx.interaction))

    @bot.hybrid_command(name="pause", aliases=["pa"], description="Pausa a musica atual.")
    async def pause(ctx: commands.Context) -> None:
        voice_client = ctx.voice_client
        if not voice_client or not _voice_playing(voice_client):
            await _send(ctx, "Nao tem nenhuma musica tocando agora.")
            return

        music.player_for(ctx.guild.id).pause() if ctx.guild else None
        if hasattr(voice_client, "pause") and voice_client.__class__.__module__.startswith("wavelink"):
            await voice_client.pause(True)
        else:
            voice_client.pause()
        await _send(ctx, "Musica pausada.")

    @bot.hybrid_command(name="resume", aliases=["r", "continuar"], description="Continua a musica pausada.")
    async def resume(ctx: commands.Context) -> None:
        voice_client = ctx.voice_client
        if not voice_client or not _voice_paused(voice_client):
            await _send(ctx, "Nao tem nenhuma musica pausada.")
            return

        music.player_for(ctx.guild.id).resume() if ctx.guild else None
        if hasattr(voice_client, "pause") and voice_client.__class__.__module__.startswith("wavelink"):
            await voice_client.pause(False)
        else:
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
            start = 2 if player.current else 1
            lines.extend(_format_queue(upcoming, start=start))

        await _send(ctx, "\n".join(lines) if lines else "A fila esta vazia.")

    @bot.hybrid_command(
        name="autoplay",
        aliases=["autoqueue", "playqueue", "filaauto"],
        description="Controla o autoplay ou monta uma fila com seu Last.fm.",
    )
    async def autoplay(ctx: commands.Context, modo: str = "status") -> None:
        if not ctx.guild:
            await _send(ctx, "Esse comando so funciona em servidores.")
            return
        player = music.player_for(ctx.guild.id)
        normalized = modo.lower()
        if normalized in {"on", "off", "status"}:
            if normalized == "status":
                await _send(ctx, f"Autoplay: `{'ativado' if player.autoplay_enabled else 'desativado'}`.")
            else:
                enabled = player.set_autoplay(normalized == "on")
                await _send(ctx, f"Autoplay {'ativado' if enabled else 'desativado'}.")
            return
        if not ctx.author.voice or not ctx.author.voice.channel:
            await _send(ctx, "Entre em um canal de voz primeiro.")
            return
        try:
            quantidade = max(1, min(10, int(normalized)))
        except ValueError:
            await _send(ctx, "Use `on`, `off`, `status` ou uma quantidade entre 1 e 10.")
            return
        recommendations = await music.recommendations_for_user(ctx.author.id, quantidade)
        if not recommendations:
            await _send(ctx, "Nao encontrei recomendacoes. Vincule/ative seu Last.fm primeiro.")
            return

        tracks = []
        for item in recommendations:
            artist_data = item.get("artist")
            artist = artist_data.get("name") if isinstance(artist_data, dict) else artist_data
            title = item.get("name")
            if not title:
                continue
            query = f"{artist} {title}" if artist else str(title)
            try:
                resolved = await music.resolve_tracks(query, ctx.author.display_name, ctx.author.id)
            except MusicError:
                continue
            if resolved:
                tracks.append(resolved[0])

        if not tracks:
            await _send(ctx, "O Last.fm retornou recomendacoes, mas nao consegui resolver nenhuma para reproducao.")
            return

        async with music.voice_lock_for(ctx.guild.id):
            async with player._advance_lock:
                use_lavalink = player.current.provider == "lavalink" if player.current else tracks[0].provider == "lavalink"
                voice_client = await _connect_or_move(ctx, use_lavalink=use_lavalink)
                position = player.add_many(tracks, ctx.channel)
                await player.start_if_idle(voice_client)

        await ctx.send(f"Adicionei `{len(tracks)}` recomendacao(oes) do seu Last.fm a partir da posicao `#{position}`.")

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

        await ctx.send(embed=build_now_playing_embed(player), view=MusicNowPlayingView(music, lyrics_service, player))

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
    @app_commands.choices(mode=REPEAT_CHOICES)
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
    @app_commands.choices(name=FILTER_CHOICES)
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


def _voice_playing(voice_client) -> bool:
    return bool(getattr(voice_client, "playing", False)) if voice_client.__class__.__module__.startswith("wavelink") else voice_client.is_playing()


def _voice_paused(voice_client) -> bool:
    return bool(getattr(voice_client, "paused", False)) if voice_client.__class__.__module__.startswith("wavelink") else voice_client.is_paused()


async def _connect_or_move(ctx: commands.Context, *, use_lavalink: bool = False) -> discord.VoiceClient:
    channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    try:
        if not voice_client:
            if use_lavalink:
                import wavelink
                return await channel.connect(cls=wavelink.Player)
            return await channel.connect()

        current_is_lavalink = voice_client.__class__.__module__.startswith("wavelink")
        if current_is_lavalink != use_lavalink:
            await voice_client.disconnect()
            if use_lavalink:
                import wavelink
                return await channel.connect(cls=wavelink.Player)
            return await channel.connect()

        if voice_client.channel != channel:
            await voice_client.move_to(channel)
    except RuntimeError as error:
        message = str(error).lower()
        if "davey" in message or "voice" in message:
            raise MusicError("A dependencia de voz esta faltando. Rode `pip install -r requirements.txt` e recrie o container.") from error
        raise

    return voice_client


def _format_queue(tracks: list[Track], *, start: int = 1) -> list[str]:
    visible_tracks = tracks[:10]
    lines = [f"{index}. **{track.title}** - pedido por `{track.requested_by}`" for index, track in enumerate(visible_tracks, start)]
    if len(tracks) > len(visible_tracks):
        lines.append(f"...e mais {len(tracks) - len(visible_tracks)} musica(s).")
    return lines


def _queued_embed(track: Track, position: int) -> discord.Embed:
    title = "Preparando musica" if position == 1 else "Musica adicionada a fila"
    embed = discord.Embed(
        title=title,
        description=f"**[{track.title}]({track.webpage_url})**",
        color=0x1DB954,
    )
    embed.add_field(name="Posicao", value=f"`#{position}`", inline=True)
    if track.duration:
        embed.add_field(name="Duracao", value=f"`{_format_duration(track.duration)}`", inline=True)
    if track.artist:
        embed.add_field(name="Artista", value=track.artist, inline=True)
    if track.thumbnail_url:
        embed.set_thumbnail(url=track.thumbnail_url)
    return embed


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


class MusicNowPlayingView(discord.ui.View):
    def __init__(self, music: MusicService, lyrics_service: LyricsService, player) -> None:
        super().__init__(timeout=300)
        self._music = music
        self._lyrics_service = lyrics_service
        self._player = player

    @discord.ui.button(label="Pausar", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if not voice_client or not _voice_playing(voice_client):
            await interaction.response.send_message("Nao tem musica tocando agora.", ephemeral=True)
            return
        self._player.pause()
        if voice_client.__class__.__module__.startswith("wavelink"):
            await voice_client.pause(True)
        else:
            voice_client.pause()
        await interaction.response.send_message("Musica pausada.", ephemeral=True)

    @discord.ui.button(label="Continuar", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def resume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if not voice_client or not _voice_paused(voice_client):
            await interaction.response.send_message("Nao tem musica pausada.", ephemeral=True)
            return
        self._player.resume()
        if voice_client.__class__.__module__.startswith("wavelink"):
            await voice_client.pause(False)
        else:
            voice_client.resume()
        await interaction.response.send_message("Musica retomada.", ephemeral=True)

    @discord.ui.button(label="Pular", emoji="⏭️", style=discord.ButtonStyle.primary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if not interaction.guild or not voice_client:
            await interaction.response.send_message("Nao estou tocando nada neste servidor.", ephemeral=True)
            return
        try:
            await self._music.player_for(interaction.guild.id).skip(voice_client)
        except MusicError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message("Musica pulada.", ephemeral=True)

    @discord.ui.button(label="Parar", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if not interaction.guild or not voice_client:
            await interaction.response.send_message("Nao estou em um canal de voz.", ephemeral=True)
            return
        await self._music.player_for(interaction.guild.id).stop(voice_client)
        await interaction.response.send_message("Fila encerrada e desconectei do canal de voz.", ephemeral=True)

    @discord.ui.button(label="-10s", emoji="⏪", style=discord.ButtonStyle.secondary, row=1)
    async def rewind_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._seek_from_button(interaction, -10)

    @discord.ui.button(label="+10s", emoji="⏩", style=discord.ButtonStyle.secondary, row=1)
    async def forward_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._seek_from_button(interaction, 10)

    @discord.ui.button(label="Vol -", emoji="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def volume_down_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._volume_from_button(interaction, -10)

    @discord.ui.button(label="Vol +", emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_up_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._volume_from_button(interaction, 10)

    @discord.ui.button(label="Fila", emoji="📜", style=discord.ButtonStyle.secondary, row=2)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse controle funciona dentro de servidores.", ephemeral=True)
            return
        player = self._music.player_for(interaction.guild.id)
        lines = []
        if player.current:
            lines.append(f"1. **{player.current.title}** `{_format_duration(player.current_position())}`")
        upcoming = player.queue_snapshot()
        if upcoming:
            lines.extend(_format_queue(upcoming, start=2 if player.current else 1))
        await interaction.response.send_message("\n".join(lines) if lines else "A fila esta vazia.", ephemeral=True)

    @discord.ui.button(label="Shuffle", emoji="🔀", style=discord.ButtonStyle.secondary, row=2)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse controle funciona dentro de servidores.", ephemeral=True)
            return
        total = self._music.player_for(interaction.guild.id).shuffle()
        await interaction.response.send_message(f"Fila embaralhada com `{total}` musica(s).", ephemeral=True)

    @discord.ui.button(label="Repeat", emoji="🔁", style=discord.ButtonStyle.secondary, row=2)
    async def repeat_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse controle funciona dentro de servidores.", ephemeral=True)
            return
        player = self._music.player_for(interaction.guild.id)
        next_mode = {
            RepeatMode.OFF: RepeatMode.ONE,
            RepeatMode.ONE: RepeatMode.ALL,
            RepeatMode.ALL: RepeatMode.OFF,
        }[player.repeat]
        player.set_repeat(next_mode)
        await interaction.response.send_message(f"Repeat definido como `{next_mode.value}`.", ephemeral=True)

    @discord.ui.button(label="Lyrics", emoji="🎤", style=discord.ButtonStyle.primary, row=2)
    async def lyrics_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        track = self._player.current
        if not track:
            await interaction.followup.send("Nao tem musica tocando agora.")
            return
        try:
            result = await self._lyrics_service.find_for_track(track)
        except LyricsError as error:
            await interaction.followup.send(str(error))
            return

        pages = _lyrics_pages(result.lyrics)
        await interaction.followup.send(
            embed=_lyrics_embed(result.title, result.artist, pages, 0),
            view=LyricsView(result.title, result.artist, pages),
        )

    async def _seek_from_button(self, interaction: discord.Interaction, seconds: int) -> None:
        voice_client = interaction.guild.voice_client if interaction.guild else None
        if not interaction.guild or not voice_client:
            await interaction.response.send_message("Nao estou tocando nada neste servidor.", ephemeral=True)
            return
        try:
            position = await self._music.player_for(interaction.guild.id).seek_relative(voice_client, seconds)
        except MusicError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message(f"Tempo ajustado para `{_format_duration(position)}`.", ephemeral=True)

    async def _volume_from_button(self, interaction: discord.Interaction, delta: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Esse controle funciona dentro de servidores.", ephemeral=True)
            return
        player = self._music.player_for(interaction.guild.id)
        try:
            value = player.set_volume(player.volume_percent + delta)
        except MusicError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message(f"Volume ajustado para `{value}%`.", ephemeral=True)
