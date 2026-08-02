import re
from dataclasses import dataclass

import aiohttp

from bot.services.music_player import Track


LRCLIB_BASE_URL = "https://lrclib.net/api"
USER_AGENT = "AylaBot/1.0"


class LyricsError(Exception):
    pass


@dataclass(frozen=True)
class LyricsResult:
    title: str
    artist: str | None
    lyrics: str
    synced: bool = False


class LyricsService:
    async def find_for_track(self, track: Track) -> LyricsResult:
        queries = _track_queries(track)
        for query in queries:
            try:
                result = await self.search(query, duration=track.duration)
            except LyricsError:
                continue
            return result

        raise LyricsError("Nao encontrei letra para a musica atual.")

    async def search(self, query: str, duration: int | None = None) -> LyricsResult:
        records = await self._search_lrclib(query)
        if not records:
            raise LyricsError("Nao encontrei letra para essa busca.")

        record = _best_record(records, query, duration)
        lyrics = _plain_lyrics(record)
        if not lyrics:
            raise LyricsError("Encontrei a musica, mas ela nao tem letra disponivel.")

        return LyricsResult(
            title=record.get("trackName") or query,
            artist=record.get("artistName"),
            lyrics=lyrics,
            synced=bool(record.get("syncedLyrics")),
        )

    async def _search_lrclib(self, query: str) -> list[dict]:
        async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
            async with session.get(f"{LRCLIB_BASE_URL}/search", params={"q": query}, timeout=12) as response:
                if response.status == 404:
                    return []
                if response.status != 200:
                    raise LyricsError(f"A fonte de letras retornou erro {response.status}.")
                data = await response.json(content_type=None)

        return data if isinstance(data, list) else []


def _track_queries(track: Track) -> list[str]:
    queries = []
    if track.artist:
        queries.append(f"{track.artist} {track.title}")
    queries.append(track.title)

    cleaned = _clean_title(track.title)
    if cleaned and cleaned != track.title:
        if track.artist:
            queries.append(f"{track.artist} {cleaned}")
        queries.append(cleaned)

    return list(dict.fromkeys(queries))


def _best_record(records: list[dict], query: str, duration: int | None) -> dict:
    normalized_query = _normalize_text(query)

    def text_score(record: dict) -> int:
        haystack = _normalize_text(f"{record.get('artistName', '')} {record.get('trackName', '')}")
        if normalized_query == haystack:
            return 0
        if normalized_query in haystack:
            return 1
        return 2

    if duration is None:
        return min(records, key=text_score)

    def score(record: dict) -> tuple[int, int]:
        record_duration = record.get("duration")
        if not isinstance(record_duration, (int, float)):
            return (text_score(record), 999999)
        return (text_score(record), abs(int(record_duration) - duration))

    return min(records, key=score)


def _plain_lyrics(record: dict) -> str | None:
    plain = record.get("plainLyrics")
    if isinstance(plain, str) and plain.strip():
        return plain.strip()

    synced = record.get("syncedLyrics")
    if isinstance(synced, str) and synced.strip():
        lines = []
        for line in synced.splitlines():
            line = re.sub(r"^\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]\s*", "", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip() or None

    return None


def _clean_title(title: str) -> str:
    cleaned = re.sub(r"\([^)]*(official|lyrics?|audio|video|visualizer|remaster|sped up|slowed)[^)]*\)", "", title, flags=re.I)
    cleaned = re.sub(r"\[[^\]]*(official|lyrics?|audio|video|visualizer|remaster|sped up|slowed)[^\]]*\]", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -|")


def _normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
