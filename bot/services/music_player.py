import asyncio
import random
from collections import deque
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Callable
from urllib.parse import urlparse

import aiohttp
import discord
import yt_dlp


MAX_PLAYLIST_TRACKS = 50
DEFAULT_VOLUME = 0.6
FILTERS = {
    "none": None,
    "bassboost": "bass=g=8",
    "nightcore": "asetrate=48000*1.15,aresample=48000",
    "vaporwave": "asetrate=48000*0.85,aresample=48000",
    "soft": "lowpass=f=12000,volume=0.9",
}

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "default_search": "ytsearch1",
    "noplaylist": False,
    "ignoreerrors": True,
    "extract_flat": False,
    "js_runtimes": {"node": {}},
}

FFMPEG_RECONNECT_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


class RepeatMode(str, Enum):
    OFF = "off"
    ONE = "one"
    ALL = "all"


class MusicError(Exception):
    pass


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requested_by: str
    duration: int | None = None
    artist: str | None = None
    album: str | None = None
    thumbnail_url: str | None = None


class GuildMusicPlayer:
    def __init__(self, bot, guild_id: int) -> None:
        self._bot = bot
        self._guild_id = guild_id
        self._queue: deque[Track] = deque()
        self._current: Track | None = None
        self._current_started_at: float | None = None
        self._current_seek: int = 0
        self._source: discord.PCMVolumeTransformer | None = None
        self._text_channel: discord.abc.Messageable | None = None
        self._now_playing_view_factory: Callable[["GuildMusicPlayer"], discord.ui.View] | None = None
        self._volume = DEFAULT_VOLUME
        self._repeat = RepeatMode.OFF
        self._filter_name = "none"
        self._suppress_after = False

    @property
    def current(self) -> Track | None:
        return self._current

    @property
    def volume_percent(self) -> int:
        return round(self._volume * 100)

    @property
    def repeat(self) -> RepeatMode:
        return self._repeat

    @property
    def filter_name(self) -> str:
        return self._filter_name

    def queue_snapshot(self) -> list[Track]:
        return list(self._queue)

    def current_position(self) -> int:
        if not self._current_started_at:
            return self._current_seek
        return self._current_seek + max(0, round(monotonic() - self._current_started_at))

    def add_many(self, tracks: list[Track], text_channel: discord.abc.Messageable) -> int:
        self._text_channel = text_channel
        first_position = self.queue_size(include_current=True) + 1
        self._queue.extend(tracks)
        return first_position

    def set_now_playing_view_factory(self, factory: Callable[["GuildMusicPlayer"], discord.ui.View]) -> None:
        self._now_playing_view_factory = factory

    def queue_size(self, *, include_current: bool = False) -> int:
        return len(self._queue) + (1 if include_current and self._current else 0)

    async def start_if_idle(self, voice_client: discord.VoiceClient) -> None:
        if not voice_client.is_playing() and not voice_client.is_paused():
            await self._play_next(voice_client)

    async def skip(self, voice_client: discord.VoiceClient) -> None:
        if not voice_client.is_playing() and not voice_client.is_paused():
            raise MusicError("Nao tem nenhuma musica tocando agora.")
        voice_client.stop()

    async def seek(self, voice_client: discord.VoiceClient, seconds: int) -> None:
        if not self._current:
            raise MusicError("Nao tem nenhuma musica tocando agora.")
        if seconds < 0:
            raise MusicError("O tempo precisa ser maior ou igual a 0.")
        if self._current.duration and seconds >= self._current.duration:
            raise MusicError("Esse tempo passa da duracao da musica.")

        self._suppress_after = True
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        self._play_current(voice_client, seek=seconds)

    async def seek_relative(self, voice_client: discord.VoiceClient, seconds: int) -> int:
        new_position = self.current_position() + seconds
        await self.seek(voice_client, max(0, new_position))
        return max(0, new_position)

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        self.clear()
        self._current = None
        self._current_started_at = None
        self._suppress_after = True
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
        await voice_client.disconnect()

    def clear(self) -> int:
        removed = len(self._queue)
        self._queue.clear()
        return removed

    def shuffle(self) -> int:
        tracks = list(self._queue)
        random.shuffle(tracks)
        self._queue = deque(tracks)
        return len(tracks)

    def remove(self, position: int) -> Track:
        if position < 1 or position > len(self._queue):
            raise MusicError("Posicao invalida na fila.")
        tracks = list(self._queue)
        removed = tracks.pop(position - 1)
        self._queue = deque(tracks)
        return removed

    def move(self, source: int, destination: int) -> Track:
        if source < 1 or source > len(self._queue) or destination < 1 or destination > len(self._queue):
            raise MusicError("Posicao invalida na fila.")
        tracks = list(self._queue)
        track = tracks.pop(source - 1)
        tracks.insert(destination - 1, track)
        self._queue = deque(tracks)
        return track

    def set_volume(self, percent: int) -> int:
        if percent < 0 or percent > 200:
            raise MusicError("Use um volume entre 0 e 200.")
        self._volume = percent / 100
        if self._source:
            self._source.volume = self._volume
        return percent

    def set_repeat(self, mode: RepeatMode) -> RepeatMode:
        self._repeat = mode
        return self._repeat

    async def set_filter(self, voice_client: discord.VoiceClient | None, name: str) -> str:
        normalized = name.lower()
        if normalized not in FILTERS:
            options = ", ".join(FILTERS)
            raise MusicError(f"Filtro invalido. Use um destes: `{options}`.")

        self._filter_name = normalized
        if voice_client and self._current and (voice_client.is_playing() or voice_client.is_paused()):
            await self.seek(voice_client, self.current_position())
        return self._filter_name

    async def _play_next(self, voice_client: discord.VoiceClient) -> None:
        if self._current and self._repeat == RepeatMode.ONE:
            self._play_current(voice_client)
            return

        if self._current and self._repeat == RepeatMode.ALL:
            self._queue.append(self._current)

        if not self._queue:
            self._current = None
            self._current_started_at = None
            return

        self._current = self._queue.popleft()
        self._play_current(voice_client)

        if self._text_channel:
            view = self._now_playing_view_factory(self) if self._now_playing_view_factory and self._current else None
            await self._text_channel.send(embed=build_now_playing_embed(self, automatic=True), view=view)

    def _play_current(self, voice_client: discord.VoiceClient, *, seek: int = 0) -> None:
        if not self._current:
            return

        before_options = FFMPEG_RECONNECT_OPTIONS
        if seek:
            before_options = f"-ss {seek} {before_options}"

        audio_filter = FILTERS[self._filter_name]
        options = "-vn"
        if audio_filter:
            options = f'-vn -filter:a "{audio_filter}"'

        audio = discord.FFmpegPCMAudio(
            self._current.stream_url,
            before_options=before_options,
            options=options,
        )
        self._source = discord.PCMVolumeTransformer(audio, volume=self._volume)
        self._current_seek = seek
        self._current_started_at = monotonic()
        voice_client.play(self._source, after=self._after_track(voice_client))

    def _after_track(self, voice_client: discord.VoiceClient) -> Callable[[Exception | None], None]:
        def callback(error: Exception | None) -> None:
            if self._suppress_after:
                self._suppress_after = False
                return

            if error and self._text_channel:
                asyncio.run_coroutine_threadsafe(
                    self._text_channel.send(f"Erro ao tocar a musica: `{error}`"),
                    self._bot.loop,
                )

            asyncio.run_coroutine_threadsafe(self._play_next(voice_client), self._bot.loop)

        return callback


class MusicService:
    def __init__(self, bot) -> None:
        self._bot = bot
        self._players: dict[int, GuildMusicPlayer] = {}

    def player_for(self, guild_id: int) -> GuildMusicPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildMusicPlayer(self._bot, guild_id)
        return self._players[guild_id]

    async def resolve_tracks(self, query: str, requested_by: str) -> list[Track]:
        normalized_query = await self._normalize_query(query)
        info = await asyncio.to_thread(self._extract_best_info, normalized_query)
        entries = self._entries_from_info(info)
        if not entries:
            raise MusicError("Nao encontrei nenhum resultado para essa busca.")

        limit = MAX_PLAYLIST_TRACKS if _is_url(normalized_query) else 1
        return [self._track_from_info(entry, requested_by, normalized_query) for entry in entries[:limit]]

    def _extract_best_info(self, query: str) -> dict:
        if _is_url(query) or query.startswith(("ytsearch", "scsearch")):
            return self._extract_info(query)

        errors: list[str] = []
        for candidate in (f"ytsearch5:{query}", f"scsearch5:{query}", query):
            try:
                info = self._extract_info(candidate)
            except MusicError as error:
                errors.append(str(error))
                continue

            entries = self._entries_from_info(info)
            if entries:
                return entries[0]

        detail = "; ".join(errors[-2:])
        raise MusicError(f"Nao encontrei musica nas fontes disponiveis. {detail}")

    def _extract_info(self, query: str) -> dict:
        try:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
                return ytdl.extract_info(query, download=False)
        except Exception as error:
            raise MusicError(f"Nao consegui carregar essa musica: {error}") from error

    def _entries_from_info(self, info: dict) -> list[dict]:
        if "entries" not in info:
            return [info]
        return [entry for entry in info["entries"] if entry and entry.get("url")]

    def _track_from_info(self, info: dict, requested_by: str, fallback_url: str) -> Track:
        stream_url = info.get("url")
        if not stream_url:
            raise MusicError("Nao consegui obter o audio dessa fonte.")

        return Track(
            title=info.get("track") or info.get("title") or "Musica sem titulo",
            webpage_url=info.get("webpage_url") or fallback_url,
            stream_url=stream_url,
            requested_by=requested_by,
            duration=info.get("duration"),
            artist=info.get("artist") or info.get("creator") or info.get("uploader"),
            album=info.get("album"),
            thumbnail_url=info.get("thumbnail"),
        )

    async def _normalize_query(self, query: str) -> str:
        if not _is_url(query):
            return query

        host = urlparse(query).netloc.lower()
        if "spotify.com" in host or "deezer.com" in host:
            title = await self._resolve_oembed_title(query)
            if title:
                return f"ytsearch1:{title}"
            raise MusicError("Nao consegui ler esse link do Spotify/Deezer. Tente buscar pelo nome da musica.")

        return query

    async def _resolve_oembed_title(self, url: str) -> str | None:
        endpoints = [
            "https://open.spotify.com/oembed",
            "https://noembed.com/embed",
        ]
        async with aiohttp.ClientSession() as session:
            for endpoint in endpoints:
                try:
                    async with session.get(endpoint, params={"url": url}, timeout=10) as response:
                        if response.status != 200:
                            continue
                        data = await response.json(content_type=None)
                except Exception:
                    continue

                title = data.get("title") if isinstance(data, dict) else None
                if isinstance(title, str) and title.strip():
                    return title.strip()

        return None


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def build_now_playing_embed(player: GuildMusicPlayer, *, automatic: bool = False) -> discord.Embed:
    track = player.current
    title = "Tocando agora" if automatic else "Now playing"
    embed = discord.Embed(title=title, color=0x1DB954)
    if not track:
        embed.description = "Nao tem nenhuma musica tocando agora."
        return embed

    duration = _format_track_time(player.current_position(), track.duration)
    embed.description = f"**[{track.title}]({track.webpage_url})**"
    if track.artist:
        embed.add_field(name="Artista", value=track.artist, inline=True)
    if track.album:
        embed.add_field(name="Album", value=track.album, inline=True)
    embed.add_field(name="Tempo", value=f"`{duration}`", inline=True)
    embed.add_field(name="Volume", value=f"`{player.volume_percent}%`", inline=True)
    embed.add_field(name="Repeat", value=f"`{player.repeat.value}`", inline=True)
    embed.add_field(name="Filtro", value=f"`{player.filter_name}`", inline=True)
    embed.add_field(name="Pedido por", value=f"`{track.requested_by}`", inline=True)
    if track.thumbnail_url:
        embed.set_thumbnail(url=track.thumbnail_url)
    embed.set_footer(text=f"{player.queue_size()} musica(s) aguardando na fila")
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
