import asyncio
import math
import random
import time
from functools import partial
import shlex
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Callable
from urllib.parse import urlparse

import aiohttp
import discord
import yt_dlp

from bot.config import Settings


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
    requester_id: int | None = None
    http_headers: dict[str, str] | None = None


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
        self._stream_retry_attempted = False

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

    def pause(self) -> None:
        if self._current_started_at is not None:
            self._current_seek = self.current_position()
            self._current_started_at = None

    def resume(self) -> None:
        if self._current and self._current_started_at is None:
            self._current_started_at = monotonic()

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
        self._play_current(voice_client, seek=seconds, new_playback=False)

    async def seek_relative(self, voice_client: discord.VoiceClient, seconds: int) -> int:
        new_position = self.current_position() + seconds
        await self.seek(voice_client, max(0, new_position))
        return max(0, new_position)

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        self._bot._lastfm_scrobbler.ended(self._guild_id, self.current_position()) if hasattr(self._bot, "_lastfm_scrobbler") else None
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
        self._stream_retry_attempted = False
        self._play_current(voice_client)

        if self._text_channel:
            view = self._now_playing_view_factory(self) if self._now_playing_view_factory and self._current else None
            # .fmbot's message-based reader looks for the conventional English
            # "Started playing ... by ..." announcement used by music bots.
            # The embed remains the user-facing Ayla UI, while this content gives
            # compatible readers a stable, unambiguous track announcement.
            track = self._current
            announcement = f"Started playing {track.title}"
            if track.artist:
                announcement += f" by {track.artist}"
            await self._text_channel.send(announcement[:1900], embed=build_now_playing_embed(self, automatic=True), view=view)

    def _play_current(self, voice_client: discord.VoiceClient, *, seek: int = 0, new_playback: bool = True) -> None:
        if not self._current:
            return

        before_options = FFMPEG_RECONNECT_OPTIONS
        if self._current.http_headers:
            header_lines = []
            for key, value in self._current.http_headers.items():
                key = str(key).replace("\r", "").replace("\n", "").strip()
                value = str(value).replace("\r", "").replace("\n", "").strip()
                if key and value and key.lower() in {"user-agent", "referer", "origin", "accept", "accept-language"}:
                    header_lines.append(f"{key}: {value}")
            if header_lines:
                before_options += f" -headers {shlex.quote(chr(13) + chr(10).join(header_lines) + chr(13) + chr(10))}"
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
        scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
        if scrobbler and new_playback:
            listeners = tuple(member.id for member in getattr(getattr(voice_client, "channel", None), "members", []) if not member.bot)
            scrobbler.started(self._guild_id, self._current, self._current.requester_id, int(time.time()), self.current_position, listeners)

    def _after_track(self, voice_client: discord.VoiceClient) -> Callable[[Exception | None], None]:
        def callback(error: Exception | None) -> None:
            if self._suppress_after:
                self._suppress_after = False
                return

            stream_rejected = _is_stream_rejected(error)
            if error and self._text_channel:
                error_message = "O servidor do áudio recusou o stream. Tentando renovar..." if stream_rejected else "Erro ao tocar a musica."
                asyncio.run_coroutine_threadsafe(
                    self._text_channel.send(error_message),
                    self._bot.loop,
                )

            if stream_rejected and not self._stream_retry_attempted:
                self._stream_retry_attempted = True
                self._current_started_at = None
                asyncio.run_coroutine_threadsafe(self._retry_current_stream(voice_client), self._bot.loop)
                return

            scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
            if scrobbler:
                position = self.current_position()
                self._bot.loop.call_soon_threadsafe(partial(scrobbler.ended, self._guild_id, position, error=error))
            self._current_started_at = None

            asyncio.run_coroutine_threadsafe(self._play_next(voice_client), self._bot.loop)

        return callback

    async def _retry_current_stream(self, voice_client: discord.VoiceClient) -> None:
        track = self._current
        if not track:
            await self._play_next(voice_client)
            return
        position = self.current_position()
        try:
            await self._bot._music_service.refresh_track(track)
            self._play_current(voice_client, seek=position, new_playback=False)
            return
        except MusicError as error:
            if self._text_channel:
                await self._text_channel.send(f"Nao consegui renovar o stream da musica: `{error}`")
        scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
        if scrobbler:
            scrobbler.ended(self._guild_id, position)
        await self._play_next(voice_client)


class MusicService:
    def __init__(self, bot, settings: Settings) -> None:
        self._bot = bot
        self._settings = settings
        self._players: dict[int, GuildMusicPlayer] = {}

    def player_for(self, guild_id: int) -> GuildMusicPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildMusicPlayer(self._bot, guild_id)
        return self._players[guild_id]

    def validate_youtube_cookies(self) -> tuple[bool, str]:
        """Valida o caminho e o cabecalho Netscape sem expor o conteudo dos cookies."""
        cookies_path = self._settings.youtube_cookies_path
        if not cookies_path:
            return False, "YOUTUBE_COOKIES_PATH nao foi configurado."

        path = Path(cookies_path)
        if not path.is_file():
            return False, f"Arquivo de cookies nao encontrado: {path}"

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as cookies_file:
                first_line = cookies_file.readline().strip()
        except (OSError, UnicodeError) as error:
            return False, f"Nao consegui ler o arquivo de cookies: {error}"

        if first_line not in {"# HTTP Cookie File", "# Netscape HTTP Cookie File"}:
            return False, "O arquivo nao esta no formato Mozilla/Netscape (cookies.txt)."

        return True, f"Arquivo de cookies encontrado e com formato valido: {path}"

    async def resolve_tracks(self, query: str, requested_by: str, requester_id: int | None = None) -> list[Track]:
        normalized_query = await self._normalize_query(query)
        info = await asyncio.to_thread(self._extract_best_info, normalized_query)
        entries = self._entries_from_info(info)
        if not entries:
            raise MusicError("Nao encontrei nenhum resultado para essa busca.")

        limit = MAX_PLAYLIST_TRACKS if _is_url(normalized_query) else 1
        return [self._track_from_info(entry, requested_by, normalized_query, requester_id) for entry in entries[:limit]]

    async def test_youtube_cookies(self, query: str) -> tuple[bool, str]:
        """Testa o arquivo de cookies contra uma URL, sem retornar dados sensiveis."""
        valid, message = self.validate_youtube_cookies()
        if not valid:
            return False, message
        try:
            info = await asyncio.to_thread(self._extract_info, query)
        except MusicError as error:
            return False, str(error)
        entries = self._entries_from_info(info)
        if not entries:
            return False, "O YouTube nao retornou dados para essa URL."
        return True, "Cookies aceitos pelo YouTube para essa URL."

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
            with yt_dlp.YoutubeDL(self._ytdl_options()) as ytdl:
                info = ytdl.extract_info(query, download=False)
        except MusicError:
            raise
        except Exception as error:
            raise MusicError(_friendly_ytdl_error(error)) from error

        if info is None:
            raise MusicError("Nao consegui carregar essa musica. O YouTube pode ter bloqueado temporariamente a extracao; tente de novo mais tarde ou use outro link/fonte.")

        return info

    def _ytdl_options(self) -> dict:
        options = dict(YTDL_OPTIONS)
        js_path = self._settings.youtube_js_runtime_path
        if js_path:
            options["js_runtimes"] = {"node": {"path": js_path}}
        else:
            options["js_runtimes"] = {"node": {}}
        options["remote_components"] = ["ejs:npm"]

        cookies_path = self._settings.youtube_cookies_path
        if cookies_path:
            valid, message = self.validate_youtube_cookies()
            if not valid:
                raise MusicError(message)
            options["cookiefile"] = cookies_path

        return options

    def _entries_from_info(self, info: dict | None) -> list[dict]:
        if not info:
            return []
        if "entries" not in info:
            return [info]
        return [entry for entry in info["entries"] if entry and entry.get("url")]

    def _track_from_info(self, info: dict, requested_by: str, fallback_url: str, requester_id: int | None = None) -> Track:
        stream_url = info.get("url")
        if not stream_url:
            raise MusicError("Nao consegui obter o audio dessa fonte.")

        return Track(
            title=info.get("track") or info.get("title") or "Musica sem titulo",
            webpage_url=info.get("webpage_url") or fallback_url,
            stream_url=stream_url,
            requested_by=requested_by,
            duration=_coerce_duration(info.get("duration")),
            artist=info.get("artist") or info.get("creator") or info.get("uploader"),
            album=info.get("album"),
            thumbnail_url=info.get("thumbnail"),
            requester_id=requester_id,
            http_headers=info.get("http_headers"),
        )

    async def refresh_track(self, track: Track) -> None:
        if not track.webpage_url:
            raise MusicError("A faixa não possui uma URL de origem para renovar o stream.")
        info = await asyncio.to_thread(self._extract_info, track.webpage_url)
        entries = self._entries_from_info(info)
        if not entries:
            raise MusicError("Não consegui renovar o stream da faixa.")
        refreshed = self._track_from_info(entries[0], track.requested_by, track.webpage_url, track.requester_id)
        track.stream_url = refreshed.stream_url
        track.http_headers = refreshed.http_headers

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


def _is_stream_rejected(error: Exception | None) -> bool:
    if error is None:
        return False
    message = str(error).lower()
    return (
        "403" in message
        or "http error 400" in message
        or "server returned 400" in message
        or "return code of 8" in message
        or "return code 8" in message
    )


def _friendly_ytdl_error(error: Exception) -> str:
    message = str(error)
    normalized = message.lower()
    if "too many requests" in normalized or "http error 429" in normalized:
        return "O YouTube limitou temporariamente as requisicoes do bot (HTTP 429). Tente outra fonte, aguarde um pouco ou configure cookies do YouTube no bot."
    if "sign in to confirm" in normalized or "not a bot" in normalized or "cookies" in normalized:
        return "O YouTube pediu verificacao anti-bot. Configure `YOUTUBE_COOKIES_PATH` com um arquivo cookies.txt exportado de uma conta do YouTube, ou use SoundCloud/outro link."
    return f"Nao consegui carregar essa musica: {message}"


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


def _coerce_duration(value: object) -> int | None:
    """Converte duracoes do yt-dlp (normalmente float) para segundos inteiros."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds):
        return None
    if seconds < 0:
        return 0
    return int(seconds)


def _format_track_time(position: int | float, duration: int | float | None) -> str:
    if duration is None:
        return _format_duration(position)
    return f"{_format_duration(position)} / {_format_duration(duration)}"


def _format_duration(seconds: int | float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes}:{remaining_seconds:02d}"
