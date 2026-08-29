from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import time
from urllib.parse import urlencode

import aiohttp

from bot.config import Settings
from bot.services.lastfm_repository import LastFmAccount, LastFmRepository

log = logging.getLogger(__name__)
API_URL = "https://ws.audioscrobbler.com/2.0/"


def make_api_sig(params: dict[str, str], secret: str) -> str:
    payload = "".join(f"{key}{params[key]}" for key in sorted(params) if key not in {"api_sig", "format"})
    return hashlib.md5((payload + secret).encode(), usedforsecurity=False).hexdigest()


class LastFmError(Exception):
    def __init__(self, message: str, code: int | None = None, temporary: bool = False) -> None:
        super().__init__(message)
        self.code, self.temporary = code, temporary


class LastFmService:
    def __init__(self, settings: Settings, repository: LastFmRepository | None = None) -> None:
        self.settings = settings
        self.repository = repository or LastFmRepository(settings.lastfm_database_path)
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        return bool(self.settings.lastfm_enabled and self.settings.lastfm_api_key and self.settings.lastfm_api_secret and self.settings.lastfm_callback_url)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _request(self, params: dict[str, str], *, write: bool = False) -> dict:
        if not self.available:
            raise LastFmError("A integração Last.fm está indisponível.")
        params = {**params, "api_key": self.settings.lastfm_api_key or ""}
        params["api_sig"] = make_api_sig(params, self.settings.lastfm_api_secret or "")
        params["format"] = "json"
        if self._session is None:
            self._session = aiohttp.ClientSession(headers={"User-Agent": "AylaBot/1.0 (Last.fm integration)"})
        try:
            request = self._session.post if write else self._session.get
            async with request(API_URL, data=params if write else None, params=None if write else params, timeout=self.settings.lastfm_timeout_seconds) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise LastFmError("Falha temporária de rede no Last.fm.", temporary=True) from error
        if not isinstance(data, dict):
            raise LastFmError("Resposta inválida do Last.fm.")
        if "error" in data:
            code = int(data.get("error", 0))
            temporary = code in {8, 9, 16, 29}
            raise LastFmError(str(data.get("message", "Erro do Last.fm.")), code, temporary)
        return data

    def authorization_url(self, state: str) -> str:
        callback = f"{self.settings.lastfm_callback_url}?{urlencode({'state': state})}"
        return "https://www.last.fm/api/auth/?" + urlencode({"api_key": self.settings.lastfm_api_key or "", "cb": callback})

    async def exchange_token(self, token: str) -> tuple[str, str]:
        data = await self._request({"method": "auth.getSession", "token": token})
        session = data.get("session") or {}
        if not session.get("name") or not session.get("key"):
            raise LastFmError("O Last.fm não retornou uma sessão válida.")
        return str(session["name"]), str(session["key"])

    async def update_now_playing(self, account: LastFmAccount, track: dict[str, str | int | None]) -> None:
        params = {"method": "track.updateNowPlaying", "sk": account.session_key, "artist": str(track["artist"]), "track": str(track["track"])}
        for key in ("album", "albumArtist", "duration"):
            if track.get(key): params[key] = str(track[key])
        await self._request(params, write=True)

    async def scrobble(self, account: LastFmAccount, track: dict[str, str | int | None], timestamp: int) -> None:
        params = {"method": "track.scrobble", "sk": account.session_key, "artist": str(track["artist"]), "track": str(track["track"]), "timestamp": str(timestamp)}
        for key in ("album", "albumArtist", "duration"):
            if track.get(key): params[key] = str(track[key])
        await self._request(params, write=True)

    async def recent(self, account: LastFmAccount) -> list[dict]:
        data = await self._request({"method": "user.getRecentTracks", "user": account.username, "limit": "5"})
        return list((data.get("recenttracks") or {}).get("track") or [])[:5]

    async def now(self, account: LastFmAccount) -> dict | None:
        tracks = await self.recent(account)
        return tracks[0] if tracks else None

    async def similar(self, artist: str, track: str, limit: int = 5) -> list[dict]:
        data = await self._request({
            "method": "track.getSimilar",
            "artist": artist,
            "track": track,
            "limit": str(max(1, min(limit, 10))),
        })
        items = (data.get("similartracks") or {}).get("track") or []
        return [item for item in items if isinstance(item, dict) and item.get("name")][:limit]

    async def top_tracks(self, account: LastFmAccount, limit: int = 10) -> list[dict]:
        data = await self._request({
            "method": "user.getTopTracks",
            "user": account.username,
            "period": "overall",
            "limit": str(max(1, min(limit, 50))),
        })
        items = (data.get("toptracks") or {}).get("track") or []
        return [item for item in items if isinstance(item, dict) and item.get("name")][:limit]


def safe_page(title: str, message: str) -> str:
    return f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></main>"
