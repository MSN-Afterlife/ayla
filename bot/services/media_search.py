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
        providers = self._settings.image_provider_order or [
            "ddgs",
            "google",
            "openverse",
            "wallhaven",
            "wikimedia",
            "pexels",
            "pixabay",
            "unsplash",
            "flickr",
            "brave",
            "serpapi",
        ]
        errors: list[str] = []
        urls: list[str] = []

        for provider in providers:
            try:
                provider_urls: list[str] | None = None
                if provider == "ddgs":
                    provider_urls = await self._search_ddgs_images(query, limit)
                elif provider == "google":
                    provider_urls = await self._search_google_images(query, limit)
                elif provider == "openverse":
                    provider_urls = await self._search_openverse_images(query, limit)
                elif provider == "wallhaven":
                    provider_urls = await self._search_wallhaven_images(query, limit)
                elif provider in {"wikimedia", "commons"}:
                    provider_urls = await self._search_wikimedia_images(query, limit)
                elif provider == "pexels":
                    provider_urls = await self._search_pexels_images(query, limit)
                elif provider == "pixabay":
                    provider_urls = await self._search_pixabay_images(query, limit)
                elif provider == "unsplash":
                    provider_urls = await self._search_unsplash_images(query, limit)
                elif provider == "flickr":
                    provider_urls = await self._search_flickr_images(query, limit)
                elif provider == "brave":
                    provider_urls = await self._search_brave_images(query, limit)
                elif provider == "serpapi":
                    provider_urls = await self._search_serpapi_images(query, limit)
                else:
                    errors.append(f"{provider}: provedor desconhecido")

                if provider_urls:
                    urls.extend(_unique_urls(provider_urls, existing=urls, limit=limit - len(urls)))
                if len(urls) >= limit:
                    return urls[:limit]
            except MediaSearchError as error:
                errors.append(f"{provider}: {error}")

        if urls:
            return urls[:limit]

        details = "; ".join(errors)
        raise MediaSearchError(f"Nao encontrei imagem nas fontes configuradas. {details}")

    async def _search_ddgs_images(self, query: str, limit: int) -> list[str]:
        if DDGS is None:
            raise MediaSearchError("instale ddgs ou use IMAGE_PROVIDER_ORDER=google")

        return await asyncio.to_thread(self._search_ddgs_image_sync, query, limit)

    def _search_ddgs_image_sync(self, query: str, limit: int) -> list[str]:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=max(limit * 3, 12), safesearch="moderate"))
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

        urls = []
        for start in range(1, min(limit, 30) + 1, 10):
            params = {
                "key": api_key,
                "cx": engine_id,
                "q": query,
                "searchType": "image",
                "num": min(10, limit - len(urls)),
                "start": start,
                "safe": "active",
            }
            data = await self._get_json("https://www.googleapis.com/customsearch/v1", params)
            items = data.get("items", [])
            urls.extend(item["link"] for item in items if item.get("link"))
            if len(urls) >= limit or not items:
                break

        if not urls:
            raise MediaSearchError("Nao encontrei nenhuma imagem para essa busca.")

        return urls[:limit]

    async def _search_openverse_images(self, query: str, limit: int) -> list[str]:
        params = {
            "q": query,
            "page_size": min(limit, 50),
            "mature": "false",
            "aspect_ratio": "wide",
        }
        data = await self._get_json("https://api.openverse.org/v1/images/", params)
        return _extract_urls(data.get("results", []), ["url", "thumbnail"], limit)

    async def _search_wallhaven_images(self, query: str, limit: int) -> list[str]:
        params = {
            "q": query,
            "categories": "111",
            "purity": "100",
            "sorting": "relevance",
            "order": "desc",
            "ratios": "16x9,16x10",
            "atleast": "1280x720",
        }
        headers = None
        if self._settings.wallhaven_api_key:
            headers = {"X-API-Key": self._settings.wallhaven_api_key}

        data = await self._get_json("https://wallhaven.cc/api/v1/search", params, headers=headers)
        return _extract_urls(data.get("data", []), ["path", "thumbs.original", "thumbs.large"], limit)

    async def _search_wikimedia_images(self, query: str, limit: int) -> list[str]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrnamespace": "6",
            "gsrsearch": query,
            "gsrlimit": min(max(limit * 2, 10), 50),
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "iiurlwidth": "1600",
            "origin": "*",
        }
        data = await self._get_json("https://commons.wikimedia.org/w/api.php", params)
        pages = data.get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            raise MediaSearchError("Wikimedia retornou uma resposta inesperada.")

        urls = []
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            image_info = page.get("imageinfo")
            if not isinstance(image_info, list) or not image_info:
                continue
            first_image = image_info[0]
            if not isinstance(first_image, dict):
                continue
            mime = first_image.get("mime")
            url = first_image.get("thumburl") or first_image.get("url")
            if isinstance(mime, str) and not mime.startswith("image/"):
                continue
            if isinstance(url, str) and url.startswith("http"):
                urls.append(url)
            if len(urls) >= limit:
                return urls

        raise MediaSearchError("nenhuma imagem encontrada")

    async def _search_pexels_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.pexels_api_key
        if not api_key:
            raise MediaSearchError("configure PEXELS_API_KEY")

        params = {
            "query": query,
            "per_page": min(limit, 80),
            "orientation": "landscape",
            "locale": "pt-BR",
        }
        data = await self._get_json("https://api.pexels.com/v1/search", params, headers={"Authorization": api_key})
        return _extract_urls(data.get("photos", []), ["src.landscape", "src.large2x", "src.original"], limit)

    async def _search_pixabay_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.pixabay_api_key
        if not api_key:
            raise MediaSearchError("configure PIXABAY_API_KEY")

        params = {
            "key": api_key,
            "q": query,
            "per_page": min(max(limit, 3), 200),
            "image_type": "photo",
            "orientation": "horizontal",
            "safesearch": "true",
            "lang": "pt",
        }
        data = await self._get_json("https://pixabay.com/api/", params)
        return _extract_urls(data.get("hits", []), ["largeImageURL", "webformatURL"], limit)

    async def _search_unsplash_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.unsplash_access_key
        if not api_key:
            raise MediaSearchError("configure UNSPLASH_ACCESS_KEY")

        params = {
            "query": query,
            "per_page": min(limit, 30),
            "orientation": "landscape",
            "content_filter": "high",
        }
        headers = {"Authorization": f"Client-ID {api_key}", "Accept-Version": "v1"}
        data = await self._get_json("https://api.unsplash.com/search/photos", params, headers=headers)
        return _extract_urls(data.get("results", []), ["urls.regular", "urls.full", "urls.raw"], limit)

    async def _search_flickr_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.flickr_api_key
        if not api_key:
            raise MediaSearchError("configure FLICKR_API_KEY")

        params = {
            "method": "flickr.photos.search",
            "api_key": api_key,
            "text": query,
            "format": "json",
            "nojsoncallback": "1",
            "media": "photos",
            "safe_search": "1",
            "sort": "relevance",
            "content_type": "1",
            "extras": "url_l,url_c,url_z,url_o",
            "per_page": min(limit, 500),
        }
        data = await self._get_json("https://www.flickr.com/services/rest/", params)
        photos = data.get("photos", {}).get("photo", [])
        return _extract_urls(photos, ["url_l", "url_c", "url_z", "url_o"], limit)

    async def _search_brave_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.brave_search_api_key
        if not api_key:
            raise MediaSearchError("configure BRAVE_SEARCH_API_KEY")

        params = {
            "q": query,
            "count": min(limit, 200),
            "safesearch": "strict",
            "country": "BR",
            "search_lang": "pt-br",
        }
        headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
        data = await self._get_json("https://api.search.brave.com/res/v1/images/search", params, headers=headers)
        return _extract_urls(data.get("results", []), ["properties.url", "thumbnail.src"], limit)

    async def _search_serpapi_images(self, query: str, limit: int) -> list[str]:
        api_key = self._settings.serpapi_api_key
        if not api_key:
            raise MediaSearchError("configure SERPAPI_API_KEY")

        params = {
            "engine": "google_images",
            "api_key": api_key,
            "q": query,
            "safe": "active",
            "hl": "pt",
            "gl": "br",
            "ijn": 0,
            "imgar": "w",
        }
        data = await self._get_json("https://serpapi.com/search", params)
        return _extract_urls(data.get("images_results", []), ["original", "thumbnail"], limit)

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


def _unique_urls(urls: list[str], *, existing: list[str], limit: int) -> list[str]:
    seen = set(existing)
    unique = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
        if len(unique) >= limit:
            break
    return unique


def _extract_urls(items: Any, paths: list[str], limit: int) -> list[str]:
    if not isinstance(items, list):
        raise MediaSearchError("resposta de imagem inesperada")

    urls = []
    for item in items:
        for path in paths:
            url = _get_path(item, path)
            if isinstance(url, str) and url.startswith("http"):
                urls.append(url)
                break
        if len(urls) >= limit:
            return urls

    if urls:
        return urls
    raise MediaSearchError("nenhuma imagem encontrada")


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current
