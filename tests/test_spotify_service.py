import unittest
from unittest.mock import AsyncMock

from bot.config import Settings
from bot.services.spotify_service import SpotifyService


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self):
        self.post_calls = 0

    def post(self, *args, **kwargs):
        self.post_calls += 1
        return FakeResponse(200, {"access_token": "token", "expires_in": 3600})


class SpotifyServiceTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        return SpotifyService(Settings(
            discord_token="test",
            spotify_client_id="client",
            spotify_client_secret="secret",
        ))

    def test_parse_supported_urls_and_uris(self):
        parse = SpotifyService._parse_resource
        self.assertEqual(parse("https://open.spotify.com/track/id?si=extra"), ("track", "id"))
        self.assertEqual(parse("https://open.spotify.com/intl-pt/playlist/pl?utm_source=x"), ("playlist", "pl"))
        self.assertEqual(parse("spotify:album:album-id"), ("album", "album-id"))
        self.assertIsNone(parse("https://example.test/track/id"))

    async def test_playlist_paginates_and_preserves_order(self):
        service = self.make_service()
        service._request_url = AsyncMock(side_effect=[
            {
                "items": [{"track": {"id": "1", "name": "First", "artists": [{"name": "A"}]}}],
                "next": "https://api.spotify.com/v1/playlists/pl/tracks?offset=1",
            },
            {
                "items": [{"track": {"id": "2", "name": "Second", "artists": [{"name": "B"}]}}],
                "next": None,
            },
        ])
        kind, identifier, tracks = await service.resolve_url("https://open.spotify.com/playlist/pl")
        self.assertEqual((kind, identifier), ("playlist", "pl"))
        self.assertEqual([track.spotify_id for track in tracks], ["1", "2"])

    async def test_client_credentials_token_is_cached(self):
        service = self.make_service()
        session = FakeSession()
        service._get_session = AsyncMock(return_value=session)
        self.assertEqual(await service._get_token(), "token")
        self.assertEqual(await service._get_token(), "token")
        self.assertEqual(session.post_calls, 1)

    async def test_album_ignores_invalid_items(self):
        service = self.make_service()
        service._request_url = AsyncMock(return_value={
            "items": [
                {"id": "1", "name": "Valid", "artists": [{"name": "A"}]},
                {"name": "Missing ID", "artists": [{"name": "A"}]},
            ],
            "next": None,
        })
        _, _, tracks = await service.resolve_url("https://open.spotify.com/album/al")
        self.assertEqual([track.title for track in tracks], ["Valid"])


if __name__ == "__main__":
    unittest.main()
