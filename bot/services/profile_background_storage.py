from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
from PIL import Image, ImageOps
from PIL import UnidentifiedImageError

MAX_BACKGROUND_BYTES = 8 * 1024 * 1024
MAX_BACKGROUND_REDIRECTS = 3


class ProfileBackgroundError(Exception):
    pass


class PublicOnlyResolver(aiohttp.abc.AbstractResolver):
    """Resolve names once per connection and refuse every non-public address."""

    def __init__(self) -> None:
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records:
            raise ProfileBackgroundError("O host da imagem nao resolveu para um IP publico.")
        for record in records:
            try:
                address = ipaddress.ip_address(record["host"])
            except ValueError as error:
                raise ProfileBackgroundError("O host da imagem resolveu para um IP invalido.") from error
            if not address.is_global:
                raise ProfileBackgroundError("A URL da imagem nao pode apontar para redes privadas.")
        return records

    async def close(self) -> None:
        await self._resolver.close()


def _validate_background_url(url: str, base_url: str | None = None) -> str:
    try:
        target = urljoin(base_url, url) if base_url else url
        parsed = urlsplit(target)
    except ValueError as error:
        raise ProfileBackgroundError("A URL do background esta invalida.") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProfileBackgroundError("A URL do background precisa ser HTTP ou HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise ProfileBackgroundError("A URL do background nao pode conter credenciais.")
    try:
        port = parsed.port
    except ValueError as error:
        raise ProfileBackgroundError("A URL do background tem uma porta invalida.") from error
    expected_port = 443 if parsed.scheme == "https" else 80
    if port not in {None, expected_port}:
        raise ProfileBackgroundError("A URL do background usa uma porta nao permitida.")

    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ProfileBackgroundError("A URL da imagem nao pode apontar para redes privadas.")
    return target


class ProfileBackgroundStorage:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    async def download(self, user_id: int, url: str) -> str:
        current_url = _validate_background_url(url)
        headers = {"User-Agent": "Mozilla/5.0 AylaBot/1.0", "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"}
        try:
            connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver(), use_dns_cache=False)
            async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
                for redirect_count in range(MAX_BACKGROUND_REDIRECTS + 1):
                    async with session.get(
                        current_url,
                        timeout=aiohttp.ClientTimeout(total=20),
                        allow_redirects=False,
                    ) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location:
                                raise ProfileBackgroundError("O redirecionamento da imagem esta invalido.")
                            if redirect_count >= MAX_BACKGROUND_REDIRECTS:
                                raise ProfileBackgroundError("A imagem excedeu o limite de redirecionamentos.")
                            current_url = _validate_background_url(location, current_url)
                            continue
                        if response.status != 200:
                            raise ProfileBackgroundError(f"O servidor da imagem respondeu HTTP {response.status}.")
                        if response.content_length and response.content_length > MAX_BACKGROUND_BYTES:
                            raise ProfileBackgroundError("A imagem excede o limite de 8 MB.")
                        raw = await _read_limited(response.content)
                        break
                else:
                    raise ProfileBackgroundError("A imagem excedeu o limite de redirecionamentos.")
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
