from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from bot.services.lastfm_service import LastFmError, LastFmService

log = logging.getLogger(__name__)


@dataclass
class Playback:
    playback_id: str
    requester_id: int | None
    artist: str
    track: str
    album: str | None
    duration: int
    started_at: int
    played_seconds: float = 0
    eligible_after: float = 0
    scrobbled: bool = False
    last_position: int = 0
    position_provider: Callable[[], int] | None = None


def eligible_after(duration: int) -> float | None:
    return min(duration / 2, 240) if duration > 30 else None


def normalize_track(track) -> tuple[str, str, str | None] | None:
    artist = (track.artist or "").strip()
    title = (track.title or "").strip()
    if not artist or not title or len(artist) > 200 or len(title) > 300:
        return None
    title = re.sub(r"\s*[-|]\s*(official\s+video|official\s+audio|lyrics?)\s*$", "", title, flags=re.I).strip()
    if not title or title.lower() in {"music", "unknown", "video"}:
        return None
    return artist, title, (track.album or None)


class LastFmScrobbler:
    def __init__(self, service: LastFmService) -> None:
        self.service = service
        self._current: dict[int, Playback] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def started(self, guild_id: int, track, requester_id: int | None, started_at: int, position_provider: Callable[[], int] | None = None) -> str | None:
        if not self.service.available:
            return None
        normalized = normalize_track(track)
        duration = int(track.duration or 0)
        if not normalized or duration <= 30:
            log.info("lastfm playback ignorado guild=%s motivo=metadados_ou_duracao", guild_id)
            return None
        artist, title, album = normalized
        threshold = eligible_after(duration)
        if threshold is None:
            return None
        playback = Playback(uuid.uuid4().hex, requester_id, artist, title, album, duration, started_at, eligible_after=threshold, position_provider=position_provider)
        self._current[guild_id] = playback
        self._schedule(guild_id, playback)
        asyncio.create_task(self._now_playing(playback))
        return playback.playback_id

    def progress(self, guild_id: int, position: int) -> None:
        playback = self._current.get(guild_id)
        if playback:
            playback.last_position = max(playback.last_position, position)

    def ended(self, guild_id: int, position: int, *, error: Exception | None = None) -> None:
        playback = self._current.pop(guild_id, None)
        if not playback:
            return
        self.progress_object(playback, position)
        task = self._tasks.pop(playback.playback_id, None)
        if task: task.cancel()
        if error:
            log.warning("lastfm playback terminou com erro guild=%s playback=%s", guild_id, playback.playback_id)
        if playback.last_position >= playback.eligible_after:
            asyncio.create_task(self._scrobble(playback))
        else:
            log.info("lastfm scrobble ignorado playback=%s motivo=limite_nao_atingido", playback.playback_id)

    def progress_object(self, playback: Playback, position: int) -> None:
        playback.last_position = max(playback.last_position, position)

    def _schedule(self, guild_id: int, playback: Playback) -> None:
        async def wait_and_scrobble() -> None:
            while self._current.get(guild_id) is playback and not playback.scrobbled:
                await asyncio.sleep(2)
                if playback.position_provider:
                    playback.last_position = max(playback.last_position, playback.position_provider())
                if playback.last_position >= playback.eligible_after:
                    await self._scrobble(playback)
                    return
        self._tasks[playback.playback_id] = asyncio.create_task(wait_and_scrobble())

    async def _now_playing(self, playback: Playback) -> None:
        if playback.requester_id is None or not self.service.available: return
        account = self.service.repository.get_account(playback.requester_id)
        if not account or not account.scrobble_enabled: return
        try:
            await self.service.update_now_playing(account, {"artist": playback.artist, "track": playback.track, "album": playback.album, "duration": playback.duration})
            log.info("lastfm now playing aceito playback=%s", playback.playback_id)
        except LastFmError as error:
            self._handle_error(account.discord_user_id, error, "now_playing")

    async def _scrobble(self, playback: Playback) -> None:
        if playback.scrobbled or playback.requester_id is None: return
        lock = self._locks.setdefault(playback.requester_id, asyncio.Lock())
        async with lock:
            if playback.scrobbled: return
            account = self.service.repository.get_account(playback.requester_id)
            if not account or not account.scrobble_enabled: return
            try:
                await self.service.scrobble(account, {"artist": playback.artist, "track": playback.track, "album": playback.album, "duration": playback.duration}, playback.started_at)
                playback.scrobbled = True
                log.info("lastfm scrobble aceito playback=%s", playback.playback_id)
            except LastFmError as error:
                self._handle_error(account.discord_user_id, error, "scrobble")

    def _handle_error(self, user_id: int, error: LastFmError, operation: str) -> None:
        if error.code in {4, 9, 14, 29}:
            self.service.repository.disable(user_id)
            log.warning("lastfm sessao revogada user=%s operacao=%s", user_id, operation)
        else:
            log.warning("lastfm falha operacao=%s codigo=%s temporario=%s", operation, error.code, error.temporary)
