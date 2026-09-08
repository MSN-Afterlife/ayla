from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import deque
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
    listener_ids: tuple[int, ...] = ()
    results: dict[int, str] | None = None


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
        self._history: deque[Playback] = deque(maxlen=20)

    def started(self, guild_id: int, track, requester_id: int | None, started_at: int, position_provider: Callable[[], int] | None = None, listener_ids: tuple[int, ...] = ()) -> str | None:
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
        requester = (requester_id,) if requester_id is not None else ()
        participants = tuple(dict.fromkeys((*listener_ids, *requester)))
        playback = Playback(uuid.uuid4().hex, requester_id, artist, title, album, duration, started_at, eligible_after=threshold, position_provider=position_provider, listener_ids=participants, results={})
        self._current[guild_id] = playback
        self._history.appendleft(playback)
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
            for user_id in playback.listener_ids:
                playback.results[user_id] = f"não: limite não atingido ({playback.last_position}s/{playback.eligible_after:g}s)"
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
        if not self.service.available: return
        for user_id in playback.listener_ids:
            account = self.service.repository.get_account(user_id)
            if not account:
                continue
            if not account.scrobble_enabled:
                continue
            try:
                await self.service.update_now_playing(account, {"artist": playback.artist, "track": playback.track, "album": playback.album, "duration": playback.duration})
                log.info("lastfm now playing aceito playback=%s user=%s", playback.playback_id, user_id)
            except LastFmError as error:
                self._handle_error(account.discord_user_id, error, "now_playing")

    async def _scrobble(self, playback: Playback) -> None:
        if playback.scrobbled or not playback.listener_ids: return
        for user_id in playback.listener_ids:
            lock = self._locks.setdefault(user_id, asyncio.Lock())
            async with lock:
                account = self.service.repository.get_account(user_id)
                if not account:
                    playback.results[user_id] = "não: conta Last.fm não vinculada"
                    continue
                if not account.scrobble_enabled:
                    playback.results[user_id] = "não: scrobble desativado"
                    continue
                try:
                    await self.service.scrobble(account, {"artist": playback.artist, "track": playback.track, "album": playback.album, "duration": playback.duration}, playback.started_at)
                    playback.results[user_id] = f"sim: @{account.username}"
                    log.info("lastfm scrobble aceito playback=%s user=%s", playback.playback_id, user_id)
                except LastFmError as error:
                    playback.results[user_id] = f"não: {error} (código {error.code or 'n/d'})"
                    self._handle_error(account.discord_user_id, error, "scrobble")
        playback.scrobbled = True

    def diagnostic(self, guild_id: int, user_id: int) -> dict | None:
        for playback in self._history:
            if playback.results is not None and user_id in playback.listener_ids:
                result = playback.results.get(user_id, "aguardando processamento")
                return {"playback_id": playback.playback_id, "artist": playback.artist, "track": playback.track, "duration": playback.duration, "played": playback.last_position, "threshold": playback.eligible_after, "result": result}
        return None

    def _handle_error(self, user_id: int, error: LastFmError, operation: str) -> None:
        if error.code in {4, 9, 14, 29}:
            self.service.repository.disable(user_id)
            log.warning("lastfm sessao revogada user=%s operacao=%s", user_id, operation)
        else:
            log.warning("lastfm falha operacao=%s codigo=%s temporario=%s", operation, error.code, error.temporary)
