import asyncio
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import aiohttp
import discord
import yt_dlp


YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "quiet": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "extract_flat": False,
}

FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"


class MusicError(Exception):
    pass


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requested_by: str
    duration: int | None = None


class GuildMusicPlayer:
    def __init__(self, bot, guild_id: int) -> None:
        self._bot = bot
        self._guild_id = guild_id
        self._queue: asyncio.Queue[Track] = asyncio.Queue()
        self._current: Track | None = None
        self._text_channel: discord.abc.Messageable | None = None

    @property
    def current(self) -> Track | None:
        return self._current

    def queue_snapshot(self) -> list[Track]:
        return list(self._queue._queue)

    async def add(self, track: Track, text_channel: discord.abc.Messageable) -> int:
        self._text_channel = text_channel
        await self._queue.put(track)
        return self._queue.qsize()

    async def start_if_idle(self, voice_client: discord.VoiceClient) -> None:
        if not voice_client.is_playing() and not voice_client.is_paused():
            await self._play_next(voice_client)

    async def skip(self, voice_client: discord.VoiceClient) -> None:
        if not voice_client.is_playing() and not voice_client.is_paused():
            raise MusicError("Nao tem nenhuma musica tocando agora.")
        voice_client.stop()

    async def stop(self, voice_client: discord.VoiceClient) -> None:
        self.clear()
        self._current = None
        voice_client.stop()
        await voice_client.disconnect()

    def clear(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()

    async def _play_next(self, voice_client: discord.VoiceClient) -> None:
        if self._queue.empty():
            self._current = None
            return

        self._current = await self._queue.get()
        source = discord.FFmpegPCMAudio(
            self._current.stream_url,
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS,
        )
        voice_client.play(source, after=self._after_track(voice_client))

        if self._text_channel:
            await self._text_channel.send(f"Tocando agora: **{self._current.title}**")

    def _after_track(self, voice_client: discord.VoiceClient) -> Callable[[Exception | None], None]:
        def callback(error: Exception | None) -> None:
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

    async def resolve_track(self, query: str, requested_by: str) -> Track:
        normalized_query = await self._normalize_query(query)
        info = await asyncio.to_thread(self._extract_info, normalized_query)
        if "entries" in info:
            entries = [entry for entry in info["entries"] if entry]
            if not entries:
                raise MusicError("Nao encontrei nenhum resultado para essa busca.")
            info = entries[0]

        stream_url = info.get("url")
        if not stream_url:
            raise MusicError("Nao consegui obter o audio dessa fonte.")

        return Track(
            title=info.get("title") or "Musica sem titulo",
            webpage_url=info.get("webpage_url") or normalized_query,
            stream_url=stream_url,
            requested_by=requested_by,
            duration=info.get("duration"),
        )

    def _extract_info(self, query: str) -> dict:
        try:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
                return ytdl.extract_info(query, download=False)
        except Exception as error:
            raise MusicError(f"Nao consegui carregar essa musica: {error}") from error

    async def _normalize_query(self, query: str) -> str:
        if not _is_url(query):
            return query

        host = urlparse(query).netloc.lower()
        if "spotify.com" in host or "deezer.com" in host:
            title = await self._resolve_oembed_title(query)
            if title:
                return f"ytsearch:{title}"

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
