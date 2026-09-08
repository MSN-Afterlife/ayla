from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

from bot.config import Settings


logger = logging.getLogger(__name__)


class SpotifyError(Exception):
    pass


@dataclass(frozen=True)
class SpotifyTrack:
    spotify_id: str
    spotify_url: str
    title: str
    artists: tuple[str, ...]
    duration_ms: int | None
    album: str | None = None
    disc_number: int | None = None
    track_number: int | None = None

    @property
    def search_query(self) -> str:
        artist = ", ".join(self.artists)
        return f"{artist} - {self.title}" if artist else self.title


class SpotifyService:
    """Metadata-only Spotify client. It never downloads or plays Spotify audio."""

    API_BASE = "https://api.spotify.com/v1"
    TOKEN_URL = "https://accounts.spotify.com/api/token"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._settings.spotify_client_id and self._settings.spotify_client_secret)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def resolve_url(self, value: str) -> tuple[str, str, list[SpotifyTrack]] | None:
        resource = self._parse_resource(value)
        if resource is None:
            return None
        if not self.configured:
            raise SpotifyError("Configure SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET para usar links Spotify.")

        kind, identifier = resource
        if kind == "track":
            payload = await self._request(f"/tracks/{identifier}")
            return kind, identifier, [self._track_from_payload(payload)]

        tracks: list[SpotifyTrack] = []
        next_url = f"{self.API_BASE}/{kind}s/{identifier}/tracks?limit=50&offset=0"
        while next_url:
            payload = await self._request_url(next_url)
            for item in payload.get("items", []):
                track_payload = item.get("track") if kind == "playlist" else item
                if not isinstance(track_payload, dict) or not track_payload.get("id") or not track_payload.get("name"):
                    continue
                try:
                    tracks.append(self._track_from_payload(track_payload))
                except SpotifyError:
                    continue
            next_url = payload.get("next")
        logger.info("[SPOTIFY] %s %s: %s tracks discovered", kind, identifier, len(tracks))
        return kind, identifier, tracks

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 30:
            return self._token
        if not self.configured:
            raise SpotifyError("Spotify nao esta configurado.")
        session = await self._get_session()
        credentials = f"{self._settings.spotify_client_id}:{self._settings.spotify_client_secret}".encode()
        encoded = base64.b64encode(credentials).decode()
        try:
            async with session.post(
                self.TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {encoded}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                data = await response.json(content_type=None)
                if response.status != 200 or not data.get("access_token"):
                    raise SpotifyError("Spotify recusou as credenciais da API.")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise SpotifyError("Falha temporaria ao autenticar no Spotify.") from error
        self._token = str(data["access_token"])
        self._token_expires_at = time.monotonic() + float(data.get("expires_in", 3600))
        return self._token

    async def _request(self, path: str) -> dict:
        return await self._request_url(f"{self.API_BASE}{path}")

    async def _request_url(self, url: str) -> dict:
        session = await self._get_session()
        token = await self._get_token()
        try:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise SpotifyError("Falha temporaria ao consultar o Spotify.") from error
        if response.status == 429:
            raise SpotifyError("Spotify limitou temporariamente as requisicoes.")
        if response.status < 200 or response.status >= 300 or not isinstance(data, dict):
            raise SpotifyError("Spotify nao conseguiu carregar esse conteudo.")
        return data

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @staticmethod
    def _track_from_payload(payload: dict) -> SpotifyTrack:
        artists = tuple(
            str(item["name"])
            for item in payload.get("artists", [])
            if isinstance(item, dict) and item.get("name")
        )
        identifier = payload.get("id")
        title = payload.get("name")
        if not identifier or not title:
            raise SpotifyError("Faixa Spotify invalida.")
        return SpotifyTrack(
            spotify_id=str(identifier),
            spotify_url=str(payload.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/track/{identifier}"),
            title=str(title),
            artists=artists,
            duration_ms=int(payload["duration_ms"]) if payload.get("duration_ms") is not None else None,
            album=(payload.get("album") or {}).get("name") if isinstance(payload.get("album"), dict) else None,
            disc_number=int(payload["disc_number"]) if payload.get("disc_number") is not None else None,
            track_number=int(payload["track_number"]) if payload.get("track_number") is not None else None,
        )

    @staticmethod
    def _parse_resource(value: str) -> tuple[str, str] | None:
        if value.startswith("spotify:"):
            parts = value.split(":")
            if len(parts) == 3 and parts[1] in {"track", "album", "playlist"} and parts[2]:
                return parts[1], parts[2]
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or "spotify.com" not in parsed.netloc.lower():
            return None
        parts = [part for part in parsed.path.split("/") if part and part not in {"intl-pt", "intl-en"}]
        if len(parts) >= 2 and parts[0] in {"track", "album", "playlist"}:
            return parts[0], parts[1]
        return None
