import asyncio
import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from bot.services.profile_background_storage import (
    ProfileBackgroundError,
    PublicOnlyResolver,
    _validate_background_url,
    fetch_background_bytes,
)
from bot.services.profile_background_storage import MAX_BACKGROUND_BYTES
from bot.services.level_images import _remote_image


class FakeContent:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read(self, size):
        return next(self.chunks, b"")


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(), content_length=None, error=None):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(chunks)
        self.content_length = content_length
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *_args):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return next(self.responses)


class FakeResolver:
    def __init__(self, address):
        self.address = address

    async def resolve(self, host, port, family):
        return [{"host": self.address}]

    async def close(self):
        return None


class ProfileBackgroundSsrFTests(unittest.IsolatedAsyncioTestCase):
    def test_local_and_private_addresses_are_rejected(self):
        for url in (
            "http://127.0.0.1/image.png",
            "http://10.20.30.40/image.png",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/image.png",
            "http://[fc00::1]/image.png",
            "http://[malformed-ipv6/image.png",
        ):
            with self.subTest(url=url), self.assertRaises(ProfileBackgroundError):
                _validate_background_url(url)

    def test_redirect_targets_are_revalidated_and_nonstandard_ports_rejected(self):
        with self.assertRaises(ProfileBackgroundError):
            _validate_background_url("//127.0.0.1/metadata", "https://images.example/start")
        with self.assertRaises(ProfileBackgroundError):
            _validate_background_url("https://images.example:8443/image.png")
        self.assertEqual(
            _validate_background_url("/next.png", "https://images.example/start"),
            "https://images.example/next.png",
        )

    async def test_dns_resolver_rejects_private_answers_before_connecting(self):
        for address in ("192.168.1.10", "::1", "fe80::1"):
            resolver = PublicOnlyResolver()
            resolver._resolver = FakeResolver(address)
            with self.subTest(address=address), self.assertRaises(ProfileBackgroundError):
                await resolver.resolve("attacker.example", 443)

    async def test_dns_resolver_accepts_public_answers(self):
        resolver = PublicOnlyResolver()
        resolver._resolver = FakeResolver("93.184.216.34")

        result = await resolver.resolve("images.example", 443)

        self.assertEqual(result[0]["host"], "93.184.216.34")

    async def test_fetch_returns_limited_image_bytes(self):
        session = FakeSession([FakeResponse(chunks=[b"image-bytes"])])
        with patch("bot.services.profile_background_storage.aiohttp.ClientSession", return_value=session):
            result = await fetch_background_bytes("https://images.example/image.png")

        self.assertEqual(result, b"image-bytes")
        self.assertEqual(session.urls, ["https://images.example/image.png"])

    async def test_fetch_rejects_private_redirect_before_following_it(self):
        session = FakeSession([
            FakeResponse(status=302, headers={"Location": "https://127.0.0.1/metadata"})
        ])
        with patch("bot.services.profile_background_storage.aiohttp.ClientSession", return_value=session):
            with self.assertRaises(ProfileBackgroundError):
                await fetch_background_bytes("https://images.example/image.png")

        self.assertEqual(session.urls, ["https://images.example/image.png"])

    async def test_fetch_converts_timeout_to_user_safe_error(self):
        session = FakeSession([FakeResponse(error=asyncio.TimeoutError())])
        with patch("bot.services.profile_background_storage.aiohttp.ClientSession", return_value=session):
            with self.assertRaises(ProfileBackgroundError):
                await fetch_background_bytes("https://images.example/image.png")

    async def test_fetch_rejects_decoded_body_over_limit(self):
        session = FakeSession([
            FakeResponse(chunks=[b"x" * (MAX_BACKGROUND_BYTES + 1)])
        ])
        with patch("bot.services.profile_background_storage.aiohttp.ClientSession", return_value=session):
            with self.assertRaises(ProfileBackgroundError):
                await fetch_background_bytes("https://images.example/image.png")

    async def test_remote_image_decodes_a_valid_png(self):
        output = BytesIO()
        Image.new("RGB", (3, 2), (12, 34, 56)).save(output, format="PNG")
        with patch("bot.services.level_images.fetch_background_bytes", return_value=output.getvalue()):
            result = await _remote_image("https://images.example/image.png")

        self.assertIsNotNone(result)
        self.assertEqual(result.size, (3, 2))
        self.assertEqual(result.getpixel((0, 0)), (12, 34, 56))

    async def test_remote_image_rejects_non_image_bytes(self):
        with patch("bot.services.level_images.fetch_background_bytes", return_value=b"not an image"):
            result = await _remote_image("https://images.example/image.png")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
