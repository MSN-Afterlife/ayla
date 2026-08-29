import asyncio
from contextlib import suppress
import logging
import math
import os
import random
import shutil
import tempfile
import time
from functools import partial
import shlex
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlparse

import aiohttp
import discord
import yt_dlp

try:
    import wavelink
except ImportError:  # Lavalink e opcional durante desenvolvimento local.
    wavelink = None

from bot.config import Settings


logger = logging.getLogger(__name__)
MAX_PLAYLIST_TRACKS = 50
DEFAULT_VOLUME = 0.6
SEARCH_PROVIDERS = ("ytsearch5", "scsearch5", "gvsearch5")
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
LAVALINK_RECONNECT_GRACE_SECONDS = 20
LAVALINK_STARTUP_RETRY_DELAY_SECONDS = 2


class RepeatMode(str, Enum):
    OFF = "off"
    ONE = "one"
    ALL = "all"


class MusicError(Exception):
    pass


class ProviderError(MusicError):
    """Erro esperado de um backend de áudio."""

    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.recoverable = recoverable


WAVELINK_ERRORS = (wavelink.WavelinkException,) if wavelink is not None else ()
PLAYBACK_ERRORS = (discord.ClientException, MusicError) + WAVELINK_ERRORS


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str | None
    requested_by: str
    duration: int | None = None
    artist: str | None = None
    album: str | None = None
    thumbnail_url: str | None = None
    requester_id: int | None = None
    http_headers: dict[str, str] | None = None
    provider: str = "ytdlp"
    provider_track: Any = None
    source_query: str | None = None
    fallback_attempted: bool = False
    identifier: str | None = None


class AudioProvider:
    name = "unknown"

    async def resolve(self, query: str, requested_by: str, requester_id: int | None = None) -> list[Track]:
        raise NotImplementedError


class YtdlpProvider(AudioProvider):
    name = "ytdlp"

    def __init__(self, service: "MusicService") -> None:
        self.service = service

    async def resolve(self, query: str, requested_by: str, requester_id: int | None = None) -> list[Track]:
        try:
            info = await asyncio.to_thread(self.service._extract_best_info, query)
        except MusicError as error:
            raise ProviderError(str(error), recoverable=_is_recoverable_provider_error(str(error))) from error
        entries = self.service._entries_from_info(info)
        if not entries:
            raise ProviderError("Nao encontrei nenhum resultado para essa busca.")
        limit = MAX_PLAYLIST_TRACKS if _is_url(query) else 1
        return [self.service._track_from_info(entry, requested_by, query, requester_id) for entry in entries[:limit]]


class LavalinkProvider(AudioProvider):
    name = "lavalink"

    def __init__(self, service: "MusicService") -> None:
        self.service = service

    async def resolve(self, query: str, requested_by: str, requester_id: int | None = None) -> list[Track]:
        if not self.service.lavalink_available or wavelink is None:
            raise ProviderError("Lavalink esta indisponivel.")
        try:
            result = await wavelink.Playable.search(query)
        except (WAVELINK_ERRORS + (asyncio.TimeoutError, aiohttp.ClientError)) as error:
            raise ProviderError(_friendly_lavalink_error(error)) from error
        if not result:
            raise ProviderError("Lavalink nao encontrou essa musica.")
        entries = list(result.tracks) if isinstance(result, wavelink.Playlist) else list(result)
        limit = MAX_PLAYLIST_TRACKS if _is_url(query) else 1
        tracks: list[Track] = []
        for playable in entries[:limit]:
            tracks.append(Track(
                title=getattr(playable, "title", "Musica sem titulo"),
                webpage_url=getattr(playable, "uri", None) or query,
                stream_url=None,
                requested_by=requested_by,
                duration=_coerce_duration(getattr(playable, "length", None) / 1000 if getattr(playable, "length", None) else None),
                artist=getattr(playable, "author", None),
                thumbnail_url=getattr(playable, "artwork", None),
                requester_id=requester_id,
                provider=self.name,
                provider_track=playable,
                source_query=query,
                identifier=getattr(playable, "identifier", None),
            ))
        return tracks


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
        self._voice_client: Any = None
        self._transition_lock = asyncio.Lock()
        self._advance_lock = asyncio.Lock()
        self._ending = False
        self._lavalink_reconnect_task: asyncio.Task | None = None

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
        async with self._advance_lock:
            if not _voice_is_playing(voice_client) and not _voice_is_paused(voice_client):
                await self._play_next_impl(voice_client)

    async def skip(self, voice_client: discord.VoiceClient) -> None:
        async with self._advance_lock:
            if not _voice_is_playing(voice_client) and not _voice_is_paused(voice_client):
                raise MusicError("Nao tem nenhuma musica tocando agora.")
            if _is_lavalink_voice(voice_client):
                self._cancel_lavalink_reconnect_watchdog()
                await voice_client.skip(force=True)
            else:
                voice_client.stop()

    async def seek(self, voice_client: discord.VoiceClient, seconds: int) -> None:
        async with self._advance_lock:
            await self._seek_impl(voice_client, seconds)

    async def _seek_impl(self, voice_client: discord.VoiceClient, seconds: int) -> None:
        if not self._current:
            raise MusicError("Nao tem nenhuma musica tocando agora.")
        if seconds < 0:
            raise MusicError("O tempo precisa ser maior ou igual a 0.")
        if self._current.duration and seconds >= self._current.duration:
            raise MusicError("Esse tempo passa da duracao da musica.")

        if _voice_is_playing(voice_client) or _voice_is_paused(voice_client):
            if _is_lavalink_voice(voice_client):
                await voice_client.seek(seconds * 1000)
                self._current_seek = seconds
                self._current_started_at = None if _voice_is_paused(voice_client) else monotonic()
            else:
                self._suppress_after = True
                voice_client.stop()
                await self._play_current(voice_client, seek=seconds, new_playback=False)
        elif self._current.provider == "ytdlp":
            await self._play_current(voice_client, seek=seconds, new_playback=False)

    async def seek_relative(self, voice_client: discord.VoiceClient, seconds: int) -> int:
        new_position = self.current_position() + seconds
        await self.seek(voice_client, max(0, new_position))
        return max(0, new_position)

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        async with self._advance_lock:
            self._cancel_lavalink_reconnect_watchdog()
            self._bot._lastfm_scrobbler.ended(self._guild_id, self.current_position()) if hasattr(self._bot, "_lastfm_scrobbler") else None
            self.clear()
            self._current = None
            self._current_started_at = None
            self._suppress_after = True
            if _voice_is_playing(voice_client) or _voice_is_paused(voice_client):
                if _is_lavalink_voice(voice_client):
                    await voice_client.stop(force=True)
                    self._suppress_after = False
                else:
                    voice_client.stop()
            self._voice_client = None
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
        if _is_lavalink_voice(self._voice_client):
            asyncio.create_task(self._voice_client.set_volume(percent))
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
        if _is_lavalink_voice(voice_client):
            if normalized != "none":
                self._filter_name = "none"
                raise MusicError("Filtros avancados ainda nao estao disponiveis no Lavalink.")
            await voice_client.set_filters(None, seek=False)
            return normalized
        if voice_client and self._current and (_voice_is_playing(voice_client) or _voice_is_paused(voice_client)):
            await self.seek(voice_client, self.current_position())
        return self._filter_name

    async def _play_next(self, voice_client: discord.VoiceClient) -> None:
        async with self._advance_lock:
            await self._play_next_impl(voice_client)

    async def _play_next_impl(self, voice_client: discord.VoiceClient) -> None:
        if self._current and self._repeat == RepeatMode.ONE:
            try:
                await self._play_current(voice_client)
            except PLAYBACK_ERRORS as error:
                if self._current.provider == "ytdlp" and _is_recoverable_playback_error(error):
                    await self._fallback_current(voice_client, error, advance_locked=True, expected_track=self._current)
                else:
                    raise
            return

        if self._current and self._repeat == RepeatMode.ALL:
            self._queue.append(self._current)

        if not self._queue:
            self._current = None
            self._current_started_at = None
            return

        self._current = self._queue.popleft()
        self._stream_retry_attempted = False
        self._cancel_lavalink_reconnect_watchdog()
        try:
            async with self._transition_lock:
                voice_client = await self._ensure_voice_backend(voice_client)
                await self._play_current(voice_client)
        except PLAYBACK_ERRORS as error:
            if self._current and self._current.provider == "ytdlp" and _is_recoverable_playback_error(error):
                await self._fallback_current(voice_client, error, advance_locked=True, expected_track=self._current)
                return
            if self._text_channel:
                await self._text_channel.send("Nao consegui iniciar essa musica; pulando para a proxima.")
            self._current = None
            await self._play_next_impl(voice_client)
            return

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

    async def _play_current(self, voice_client: discord.VoiceClient, *, seek: int = 0, new_playback: bool = True) -> None:
        if not self._current:
            return
        self._voice_client = voice_client

        if self._current.provider == "lavalink":
            if not _is_lavalink_voice(voice_client) or not self._current.provider_track:
                raise MusicError("O player Lavalink nao esta conectado para esta faixa.")
            await voice_client.play(self._current.provider_track, start=seek * 1000, volume=round(self._volume * 100))
            self._cancel_lavalink_reconnect_watchdog()
            self._ending = False
            self._current_seek = seek
            self._current_started_at = monotonic()
            self._log_provider()
            self._start_scrobble(voice_client, new_playback)
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
        self._ending = False
        scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
        if scrobbler and new_playback:
            listeners = tuple(member.id for member in getattr(getattr(voice_client, "channel", None), "members", []) if not member.bot)
            scrobbler.started(self._guild_id, self._current, self._current.requester_id, int(time.time()), self.current_position, listeners)
        self._log_provider()

    def _start_scrobble(self, voice_client: discord.VoiceClient, new_playback: bool) -> None:
        scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
        if scrobbler and new_playback:
            listeners = tuple(member.id for member in getattr(getattr(voice_client, "channel", None), "members", []) if not member.bot)
            scrobbler.started(self._guild_id, self._current, self._current.requester_id, int(time.time()), self.current_position, listeners)

    def _log_provider(self) -> None:
        if self._current:
            logger.info("[MUSIC] guild=%s track=%r provider=%s fallback=%s", self._guild_id, self._current.title, self._current.provider, self._current.provider == "lavalink")

    def _after_track(self, voice_client: discord.VoiceClient) -> Callable[[Exception | None], None]:
        finished_track = self._current

        def callback(error: Exception | None) -> None:
            if self._current is not finished_track:
                return
            if self._suppress_after:
                self._suppress_after = False
                return

            stream_rejected = _is_stream_rejected(error)
            if error and self._current and self._current.provider == "ytdlp" and not stream_rejected and not self._current.fallback_attempted:
                if not _is_recoverable_playback_error(error):
                    self._ending = True
                    self._current_started_at = None
                    asyncio.run_coroutine_threadsafe(self._play_next(voice_client), self._bot.loop)
                    return
                self._ending = True
                self._current_started_at = None
                asyncio.run_coroutine_threadsafe(
                    self._fallback_current(voice_client, error, expected_track=finished_track),
                    self._bot.loop,
                )
                return

            if error and self._text_channel and (not self._current or self._current.provider != "ytdlp" or self._current.fallback_attempted):
                error_message = "O servidor do áudio recusou o stream. Tentando renovar..." if stream_rejected else "Erro ao tocar a musica."
                asyncio.run_coroutine_threadsafe(
                    self._text_channel.send(error_message),
                    self._bot.loop,
                )

            if stream_rejected and not self._stream_retry_attempted:
                self._ending = True
                self._stream_retry_attempted = True
                self._current_started_at = None
                asyncio.run_coroutine_threadsafe(self._retry_current_stream(voice_client), self._bot.loop)
                return

            if self._ending:
                return
            self._ending = True
            scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
            if scrobbler:
                position = self.current_position()
                self._bot.loop.call_soon_threadsafe(partial(scrobbler.ended, self._guild_id, position, error=error))
            self._current_started_at = None

            asyncio.run_coroutine_threadsafe(self._play_next(voice_client), self._bot.loop)

        return callback

    async def _retry_current_stream(self, voice_client: discord.VoiceClient) -> None:
        async with self._advance_lock:
            track = self._current
            if not track:
                await self._play_next_impl(voice_client)
                return
            position = self.current_position()
            try:
                await self._bot._music_service.refresh_track(track)
                if self._current is not track:
                    return
                await self._play_current(voice_client, seek=position, new_playback=False)
                return
            except MusicError as error:
                await self._fallback_current(voice_client, error, advance_locked=True, expected_track=track)
                return
            scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
            if scrobbler:
                scrobbler.ended(self._guild_id, position)
            await self._play_next_impl(voice_client)

    async def _fallback_current(
        self,
        voice_client: discord.VoiceClient,
        reason: Exception,
        *,
        advance_locked: bool = False,
        expected_track: Track | None = None,
    ) -> None:
        if not advance_locked:
            async with self._advance_lock:
                await self._fallback_current(
                    voice_client,
                    reason,
                    advance_locked=True,
                    expected_track=expected_track,
                )
            return

        advance = self._play_next_impl if advance_locked else self._play_next
        track = self._current
        if expected_track is not None and track is not expected_track:
            return
        if not track or track.fallback_attempted:
            if self._text_channel:
                await self._text_channel.send("Nao consegui tocar essa musica; o stream falhou nos provedores disponiveis.")
            await advance(voice_client)

            return

        track.fallback_attempted = True
        logger.info("[MUSIC] guild=%s yt-dlp failed: %s", self._guild_id, _safe_error(reason))
        logger.info("[MUSIC] guild=%s falling back to Lavalink", self._guild_id)
        try:
            fallback = await self._bot._music_service.resolve_with_lavalink(track.source_query or track.webpage_url, track.requested_by, track.requester_id)
            replacement = fallback[0]
            track.title, track.webpage_url = replacement.title, replacement.webpage_url
            track.duration, track.artist = replacement.duration, replacement.artist
            track.album, track.thumbnail_url = replacement.album, replacement.thumbnail_url
            track.stream_url, track.provider = None, "lavalink"
            track.provider_track, track.source_query = replacement.provider_track, replacement.source_query
            track.identifier = replacement.identifier
            channel = getattr(voice_client, "channel", None)
            if not channel or wavelink is None:
                raise MusicError("Nao consegui conectar o player Lavalink ao canal de voz.")
            if not _is_lavalink_voice(voice_client):
                await voice_client.disconnect()
                voice_client = await channel.connect(cls=wavelink.Player)
            self._current_started_at = None
            await self._play_current(voice_client, seek=self.current_position(), new_playback=False)
            logger.info("[MUSIC] guild=%s Lavalink resolved track successfully", self._guild_id)
        except Exception as error:
            logger.warning("[MUSIC] guild=%s Lavalink fallback failed: %s", self._guild_id, _safe_error(error))
            if self._text_channel:
                await self._text_channel.send("Nao consegui tocar essa musica; tente outro link ou outra busca.")
            self._current_started_at = None
            await advance(voice_client)

    async def handle_lavalink_end(self, voice_client: Any, error: Exception | None = None, event_track: Any = None) -> None:
        async with self._advance_lock:
            await self._handle_lavalink_end_impl(voice_client, error, event_track)

    async def _handle_lavalink_end_impl(self, voice_client: Any, error: Exception | None = None, event_track: Any = None) -> None:
        """Avança a fila para eventos emitidos pelo backend Lavalink."""
        if not self._current or self._current.provider != "lavalink":
            return
        if event_track is not None and self._current.provider_track is not event_track:
            return
        if self._ending:
            return
        self._cancel_lavalink_reconnect_watchdog()
        self._ending = True
        if self._suppress_after:
            self._suppress_after = False
            return
        if error and self._text_channel:
            await self._text_channel.send("O Lavalink nao conseguiu reproduzir essa musica; pulando para a proxima.")
        scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
        if scrobbler:
            scrobbler.ended(self._guild_id, self.current_position(), error=error)
        self._current_started_at = None
        await self._play_next_impl(voice_client)

    def _cancel_lavalink_reconnect_watchdog(self) -> None:
        task = self._lavalink_reconnect_task
        self._lavalink_reconnect_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def handle_lavalink_connection_lost(self, reason: str) -> None:
        """Starts a bounded recovery window for the current Lavalink track."""
        if not self._current or self._current.provider != "lavalink":
            return
        if self._lavalink_reconnect_task and not self._lavalink_reconnect_task.done():
            return
        track = self._current
        voice_client = self._voice_client
        if not voice_client:
            return
        logger.warning(
            "[MUSIC] guild=%s Lavalink connection lost during %r: %s; recovery window=%ss",
            self._guild_id,
            track.title,
            _safe_error(RuntimeError(reason)),
            LAVALINK_RECONNECT_GRACE_SECONDS,
        )
        self._lavalink_reconnect_task = asyncio.create_task(
            self._lavalink_reconnect_watchdog(track, voice_client)
        )

    async def _lavalink_reconnect_watchdog(self, track: Track, voice_client: Any) -> None:
        try:
            await asyncio.sleep(LAVALINK_RECONNECT_GRACE_SECONDS)
            async with self._advance_lock:
                if self._current is not track or track.provider != "lavalink" or self._ending:
                    return
                # Wavelink/Lavalink may restore the player without a new play call.
                # Only keep the track when the node is back and playback is active.
                music_service = getattr(self._bot, "_music_service", None)
                if music_service and music_service.lavalink_available and (
                    _voice_is_playing(voice_client) or _voice_is_paused(voice_client)
                ):
                    logger.info("[MUSIC] guild=%s Lavalink playback recovered", self._guild_id)
                    return
                self._ending = True
                scrobbler = getattr(self._bot, "_lastfm_scrobbler", None)
                if scrobbler:
                    scrobbler.ended(self._guild_id, self.current_position(), error=MusicError("Lavalink connection timeout"))
                self._current_started_at = None
                logger.warning("[MUSIC] guild=%s Lavalink recovery timed out; advancing queue", self._guild_id)
                await self._play_next_impl(voice_client)
        except asyncio.CancelledError:
            return
        finally:
            if self._lavalink_reconnect_task is asyncio.current_task():
                self._lavalink_reconnect_task = None

    async def _ensure_voice_backend(self, voice_client: Any) -> Any:
        if not self._current:
            return voice_client
        wants_lavalink = self._current.provider == "lavalink"
        if wants_lavalink == _is_lavalink_voice(voice_client):
            return voice_client
        if wavelink is None and wants_lavalink:
            raise MusicError("Lavalink nao esta disponivel.")
        channel = getattr(voice_client, "channel", None)
        if not channel:
            raise MusicError("Nao consegui manter a conexao de voz.")
        await voice_client.disconnect()
        return await channel.connect(cls=wavelink.Player) if wants_lavalink else await channel.connect()


class MusicService:
    def __init__(self, bot, settings: Settings) -> None:
        self._bot = bot
        self._settings = settings
        self._players: dict[int, GuildMusicPlayer] = {}
        self._voice_locks: dict[int, asyncio.Lock] = {}
        self._lavalink_connected = False
        self._youtube_cookies_runtime_path: Path | None = None
        self._youtube_cookies_source_signature: tuple[int, int] | None = None
        self._ytdlp = YtdlpProvider(self)
        self._lavalink = LavalinkProvider(self)

    @property
    def lavalink_available(self) -> bool:
        return bool(self._lavalink_connected and self._settings.lavalink_enabled and self._settings.lavalink_password and wavelink)

    @property
    def health(self) -> dict[str, str]:
        return {"yt-dlp": "available", "lavalink": "connected" if self.lavalink_available else "disconnected"}

    def set_lavalink_connected(self, value: bool) -> None:
        self._lavalink_connected = value

    async def connect_lavalink(self) -> bool:
        """Conecta ao Lavalink sem impedir o bot de iniciar se o servico estiver fora."""
        if not self._settings.lavalink_enabled or wavelink is None or not self._settings.lavalink_password:
            logger.info("[MUSIC] Lavalink disabled or incompletely configured; using yt-dlp only")
            return False
        if self._lavalink_connected:
            return True
        attempts = 1 + min(self._settings.lavalink_reconnect_attempts, 4) if self._settings.lavalink_reconnect else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            node = None
            try:
                node = wavelink.Node(uri=self._settings.lavalink_uri, password=self._settings.lavalink_password, retries=self._settings.lavalink_reconnect_attempts if self._settings.lavalink_reconnect else 0)
                await asyncio.wait_for(wavelink.Pool.connect(nodes=[node], client=self._bot, cache_capacity=100), timeout=5)
                self._lavalink_connected = True
                logger.info("Lavalink conectado em %s", self._settings.lavalink_uri)
                return True
            except Exception as error:
                last_error = error
                if node is not None:
                    with suppress(Exception):
                        await node.close(eject=True)
                    session = getattr(node, "_session", None)
                    if session is not None:
                        with suppress(Exception):
                            await session.close()
                if attempt + 1 < attempts:
                    await asyncio.sleep(LAVALINK_STARTUP_RETRY_DELAY_SECONDS)
        logger.warning("[MUSIC] Lavalink unavailable after %s attempt(s): %s", attempts, _safe_error(last_error or RuntimeError("unknown error")))
        return False

    def player_for(self, guild_id: int) -> GuildMusicPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildMusicPlayer(self._bot, guild_id)
        return self._players[guild_id]

    def voice_lock_for(self, guild_id: int) -> asyncio.Lock:
        return self._voice_locks.setdefault(guild_id, asyncio.Lock())

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
        logger.info("[MUSIC] resolving with yt-dlp")
        try:
            return await self._ytdlp.resolve(normalized_query, requested_by, requester_id)
        except (ProviderError, MusicError) as error:
            logger.info("[MUSIC] yt-dlp failed: %s", _safe_error(error))
            recoverable = error.recoverable if isinstance(error, ProviderError) else _is_recoverable_provider_error(str(error))
            if not recoverable:
                raise
            if not self.lavalink_available:
                raise MusicError("Nao consegui carregar essa musica nos provedores disponiveis.") from error
            logger.info("[MUSIC] falling back to Lavalink")
            try:
                return await self.resolve_with_lavalink(normalized_query, requested_by, requester_id)
            except (ProviderError, MusicError) as fallback_error:
                raise MusicError("Nao consegui carregar essa musica nos provedores disponiveis.") from fallback_error

    async def resolve_with_lavalink(self, query: str, requested_by: str, requester_id: int | None = None) -> list[Track]:
        return await self._lavalink.resolve(query, requested_by, requester_id)

    async def handle_lavalink_end(self, player: Any, *, error: Exception | None = None, event_track: Any = None) -> None:
        guild = getattr(getattr(player, "guild", None), "id", None)
        if guild is None:
            return
        music_player = self._players.get(guild)
        if music_player:
            await music_player.handle_lavalink_end(player, error, event_track)

    async def handle_lavalink_voice_closed(self, player: Any, reason: str) -> None:
        guild = getattr(getattr(player, "guild", None), "id", None)
        if guild is not None and guild in self._players:
            self._players[guild].handle_lavalink_connection_lost(reason)
            logger.warning("[MUSIC] guild=%s Lavalink voice websocket closed: %s; aguardando reconexao", guild, _safe_error(RuntimeError(reason)))

    async def handle_lavalink_node_ready(self) -> None:
        self._lavalink_connected = True

    def handle_lavalink_node_lost(self, reason: str) -> None:
        self._lavalink_connected = False
        for player in self._players.values():
            player.handle_lavalink_connection_lost(reason)

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
        for candidate in (*(f"{provider}:{query}" for provider in SEARCH_PROVIDERS), query):
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
            with yt_dlp.YoutubeDL(self._ytdl_options(query)) as ytdl:
                info = ytdl.extract_info(query, download=False)
        except MusicError:
            raise
        except Exception as error:
            raise MusicError(_friendly_ytdl_error(error)) from error

        if info is None:
            raise MusicError("Nao consegui carregar essa musica. O YouTube pode ter bloqueado temporariamente a extracao; tente de novo mais tarde ou use outro link/fonte.")

        return info

    def _ytdl_options(self, query: str | None = None) -> dict:
        options = dict(YTDL_OPTIONS)
        js_path = self._settings.youtube_js_runtime_path
        if js_path:
            options["js_runtimes"] = {"node": {"path": js_path}}
        else:
            options["js_runtimes"] = {"node": {}}
        options["remote_components"] = ["ejs:npm"]

        cookies_path = self._settings.youtube_cookies_path
        if cookies_path and _uses_youtube(query):
            valid, message = self.validate_youtube_cookies()
            if not valid:
                raise MusicError(message)
            options["cookiefile"] = self._writable_youtube_cookies_path()

        return options

    def _writable_youtube_cookies_path(self) -> str:
        """Returns a writable private copy, keeping the Docker secret read-only."""
        source = Path(self._settings.youtube_cookies_path or "")
        try:
            signature = (source.stat().st_mtime_ns, source.stat().st_size)
        except OSError as error:
            raise MusicError(f"Nao consegui acessar o arquivo de cookies: {error}") from error

        if self._youtube_cookies_runtime_path and self._youtube_cookies_source_signature == signature:
            return str(self._youtube_cookies_runtime_path)

        runtime_path = Path(tempfile.gettempdir()) / "ayla-youtube-cookies.txt"
        temporary_path = runtime_path.with_name(f".{runtime_path.name}.tmp")
        try:
            shutil.copyfile(source, temporary_path)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, runtime_path)
            os.chmod(runtime_path, 0o600)
        except OSError as error:
            with suppress(OSError):
                temporary_path.unlink()
            raise MusicError(f"Nao consegui preparar uma copia gravavel dos cookies: {error}") from error

        self._youtube_cookies_runtime_path = runtime_path
        self._youtube_cookies_source_signature = signature
        return str(runtime_path)

    def prepare_youtube_cookies(self) -> None:
        """Refreshes the writable copy when a cookie secret is available."""
        if not self._settings.youtube_cookies_path or not Path(self._settings.youtube_cookies_path).is_file():
            return
        try:
            self._writable_youtube_cookies_path()
        except MusicError as error:
            logger.warning("[MUSIC] Could not prepare writable YouTube cookies: %s", _safe_error(error))

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
            source_query=fallback_url,
            identifier=info.get("id"),
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


def _is_lavalink_voice(voice_client: Any) -> bool:
    return bool(wavelink is not None and isinstance(voice_client, wavelink.Player))


def _voice_is_playing(voice_client: Any) -> bool:
    return bool(voice_client.playing if _is_lavalink_voice(voice_client) else voice_client.is_playing())


def _voice_is_paused(voice_client: Any) -> bool:
    return bool(voice_client.paused if _is_lavalink_voice(voice_client) else voice_client.is_paused())


def _safe_error(error: Exception) -> str:
    return str(error).replace("\r", " ").replace("\n", " ")[:500]


def _friendly_lavalink_error(error: Exception) -> str:
    return f"Falha do Lavalink: {_safe_error(error)}"


def _is_recoverable_provider_error(message: str) -> bool:
    normalized = message.lower()
    non_provider = ("cookies_path", "arquivo de cookies", "formato mozilla", "dependencia", "permissao", "permission")
    if any(item in normalized for item in non_provider):
        return False
    return any(item in normalized for item in (
        "http error", "http 4", "http 5", "too many requests", "sign in to confirm",
        "not a bot", "unavailable", "blocked", "extract", "stream", "video",
        "source", "no results", "nao consegui carregar", "nao encontrei",
    ))


def _is_recoverable_playback_error(error: Exception) -> bool:
    normalized = str(error).lower()
    if any(item in normalized for item in (
        "ffmpeg was not found", "executable not found", "no such file", "permission denied",
        "already playing", "not connected", "voice client", "cannot connect",
    )):
        return False
    return _is_stream_rejected(error) or any(item in normalized for item in (
        "connection reset", "broken pipe", "process exited", "http error", "timed out",
        "input/output error", "return code",
    ))


def _uses_youtube(query: str | None) -> bool:
    if not query:
        return False
    if query.startswith("ytsearch"):
        return True
    host = urlparse(query).netloc.lower()
    return host == "youtu.be" or host.endswith("youtube.com")


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
