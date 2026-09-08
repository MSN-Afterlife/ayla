from __future__ import annotations

import asyncio
import os
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from PIL import Image, ImageOps
from PIL import UnidentifiedImageError

MAX_BACKGROUND_BYTES = 8 * 1024 * 1024


class ProfileBackgroundError(Exception):
    pass


class ProfileBackgroundStorage:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    async def download(self, user_id: int, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ProfileBackgroundError("A URL do background precisa ser HTTP ou HTTPS.")
        headers = {"User-Agent": "Mozilla/5.0 AylaBot/1.0", "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as response:
                    if response.status != 200:
                        raise ProfileBackgroundError(f"O servidor da imagem respondeu HTTP {response.status}.")
                    if response.content_length and response.content_length > MAX_BACKGROUND_BYTES:
                        raise ProfileBackgroundError("A imagem excede o limite de 8 MB.")
                    raw = await _read_limited(response.content)
        except ProfileBackgroundError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ProfileBackgroundError("Não consegui baixar essa imagem agora.") from error

        try:
            with Image.open(BytesIO(raw)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.load()
        except (OSError, UnidentifiedImageError) as error:
            raise ProfileBackgroundError("O arquivo recebido não é uma imagem válida.") from error

        filename = f"{int(user_id)}-{uuid.uuid4().hex}.png"
        target = self.directory / filename
        temporary = self.directory / f".{filename}.tmp"
        try:
            await asyncio.to_thread(image.save, temporary, "PNG", optimize=True)
            os.replace(temporary, target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ProfileBackgroundError("Não consegui salvar o background no armazenamento do bot.") from error
        finally:
            image.close()
        return str(target)


async def _read_limited(stream: aiohttp.StreamReader) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_BACKGROUND_BYTES:
            raise ProfileBackgroundError("A imagem excede o limite de 8 MB.")
        chunks.append(chunk)
