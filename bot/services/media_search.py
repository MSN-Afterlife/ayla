import asyncio
from typing import Any
from urllib.parse import urlencode

import aiohttp

from bot.config import Settings

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


class MediaSearchError(Exception):
    pass


class MediaSearch:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def search_gif(self, query: str) -> str:
        providers = self._settings.gif_provider_order or ["klipy", "apileague"]
        errors: list[str] = []

        for provider in providers:
            try:
                if provider == "klipy":
                    return await self._search_klipy_gif(query)
                if provider == "apileague":
                    return await self._search_apileague_gif(query)
                if provider == "giphy":
                    return await self._search_giphy_gif(query)

                errors.append(f"{provider}: provedor desconhecido")
            except MediaSearchError as error:
                errors.append(f"{provider}: {error}")

        details = "; ".join(errors)
        raise MediaSearchError(f"Nao encontrei GIF nas fontes configuradas. {details}")

    async def _search_klipy_gif(self, query: str) -> str:
        api_key = self._settings.klipy_api_key
        if not api_key:
            raise MediaSearchError("configure KLIPY_API_KEY")

        params = {
            "q": query,
            "query": query,
            "search_text": query,
            "limit": 1,
            "per_page": 1,
            "locale": "pt_BR",
        }
        data = await self._get_json(
            self._settings.klipy_gif_search_url,
            params,
            headers={"Authorization": f"Bearer {api_key}", "x-api-key": api_key},
        )
        return self._extract_first_url(data)

    async def _search_apileague_gif(self, query: str) -> str:
        api_key = self._settings.apileague_api_key
        if not api_key:
            raise MediaSearchError("configure APILEAGUE_API_KEY")

        params = {
            "keywords": query,
            "query": query,
            "number": 1,
        }
        data = await self._get_json(
            "https://api.apileague.com/search-gifs",
            params,
            headers={"x-api-key": api_key},
        )
        return self._extract_first_url(data)

    async def _search_giphy_gif(self, query: str) -> str:
        api_key = self._settings.giphy_api_key
        if not api_key:
            raise MediaSearchError("configure GIPHY_API_KEY")

        params = {
            "api_key": api_key,
            "q": query,
            "limit": 1,
            "rating": "pg-13",
            "lang": "pt",
        }
        data = await self._get_json("https://api.giphy.com/v1/gifs/search", params)
        return self._extract_first_url(data)

    async def search_image(self, query: str) -> str:
        results = await self.search_images(query, limit=1)
        return results[0]

    async def search_images(self, query: str, limit: int = 6) -> list[str]:
        providers = self._settings.image_provider_order or ["ddgs", "google"]
        errors: list[str] = []

        for provider in providers:
            try:
                if provider == "ddgs":
                    return await self._search_ddgs_images(query, limit)
                if provider == "google":
                    return await self._search_google_images(query, limit)

                errors.append(f"{provider}: provedor desconhecido")
            except MediaSearchError as error:
                errors.append(f"{provider}: {error}")

        details = "; ".join(errors)
        raise MediaSearchError(f"Nao encontrei imagem nas fontes configuradas. {details}")

    async def _search_ddgs_images(self, query: str, limit: int) -> list[str]:
        if DDGS is None:
            raise MediaSearchError("instale ddgs ou use IMAGE_PROVIDER_ORDER=google")

        return await asyncio.to_thread(self._search_ddgs_image_sync, query, limit)

    def _search_ddgs_image_sync(self, query: str, limit: int) -> list[str]:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=max(limit * 2, 8), safesearch="moderate"))
        except Exception as error:
            raise MediaSearchError(f"DuckDuckGo falhou: {error}") from error

        urls = []
        for item in results:
            url = item.get("image") or item.get("thumbnail")
            if isinstance(url, str) and url.startswith("http"):
                urls.append(url)
            if len(urls) >= limit:
                return urls

        raise MediaSearchError("nenhuma imagem encontrada")

    async def _search_google_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.google_search_api_key
        engine_id = self._settings.google_search_engine_id
        if not api_key or not engine_id:
            raise MediaSearchError("configure GOOGLE_SEARCH_API_KEY e GOOGLE_SEARCH_ENGINE_ID")

        params = {
            "key": api_key,
            "cx": engine_id,
            "q": query,
            "searchType": "image",
            "num": min(max(limit, 1), 10),
            "safe": "active",
        }
        data = await self._get_json("https://www.googleapis.com/customsearch/v1", params)
        items = data.get("items", [])
        if not items:
            raise MediaSearchError("Nao encontrei nenhuma imagem para essa busca.")

        return [item["link"] for item in items if item.get("link")]

    async def search_youtube(self, query: str) -> str:
        api_key = self._settings.youtube_api_key
        if not api_key:
            raise MediaSearchError("Configure YOUTUBE_API_KEY no .env para usar o comando youtube.")

        params = {
            "key": api_key,
            "part": "snippet",
            "q": query,
            "maxResults": 1,
            "type": "video",
            "safeSearch": "moderate",
        }
        data = await self._get_json("https://www.googleapis.com/youtube/v3/search", params)
        items = data.get("items", [])
        if not items:
            raise MediaSearchError("Nao encontrei nenhum video para essa busca.")

        video_id = items[0]["id"]["videoId"]
        return f"https://www.youtube.com/watch?v={video_id}"

    async def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_url = f"{url}?{urlencode(params)}"

        async with aiohttp.ClientSession() as session:
            async with session.get(request_url, headers=headers, timeout=10) as response:
                if response.status != 200:
                    details = await self._error_details(response)
                    raise MediaSearchError(f"A fonte de midia retornou erro ({response.status}). {details}")

                return await response.json()

    async def _error_details(self, response: aiohttp.ClientResponse) -> str:
        try:
            data = await response.json(content_type=None)
        except Exception:
            return "Verifique a chave da API e tente de novo."

        if not isinstance(data, dict):
            return "Verifique a chave da API e tente de novo."

        error = data.get("error", {})
        message = error.get("message") if isinstance(error, dict) else None
        status = error.get("status") if isinstance(error, dict) else None
        if message and status:
            return f"{status}: {message}"
        if message:
            return message

        return "Verifique a chave da API e tente de novo."

    def _extract_first_url(self, data: Any) -> str:
        for candidate in self._walk(data):
            if isinstance(candidate, str) and self._looks_like_gif_url(candidate):
                return candidate

        raise MediaSearchError("nenhum GIF encontrado")

    def _walk(self, value: Any):
        if isinstance(value, dict):
            preferred_keys = ["url", "gif", "gif_url", "media_url", "tinygif", "images", "data", "results", "items"]
            for key in preferred_keys:
                if key in value:
                    yield from self._walk(value[key])

            for key, child in value.items():
                if key not in preferred_keys:
                    yield from self._walk(child)
            return

        if isinstance(value, list):
            for item in value:
                yield from self._walk(item)
            return

        yield value

    def _looks_like_gif_url(self, value: str) -> bool:
        normalized = value.lower()
        return normalized.startswith("http") and (".gif" in normalized or "tenor.com" in normalized or "klipy" in normalized)
