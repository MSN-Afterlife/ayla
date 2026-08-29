import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.config import Settings
from bot.services.music_player import MusicError, MusicService, RepeatMode, Track


class MusicFallbackTests(unittest.IsolatedAsyncioTestCase):
    def make_service(self):
        bot = SimpleNamespace(loop=asyncio.get_running_loop())
        settings = Settings(discord_token="test", lavalink_enabled=True, lavalink_password="secret")
        service = MusicService(bot, settings)
        bot._music_service = service
        service._lavalink_connected = True
        return service

    async def test_ytdlp_success_does_not_call_lavalink(self):
        service = self.make_service()
        ytdlp_track = Track("yt", "https://example.test/yt", "stream", "user")
        with patch.object(service._ytdlp, "resolve", new=AsyncMock(return_value=[ytdlp_track])) as ytdlp, \
             patch.object(service._lavalink, "resolve", new=AsyncMock()) as lavalink:
            result = await service.resolve_tracks("query", "user")
        self.assertIs(result[0], ytdlp_track)
        ytdlp.assert_awaited_once()
        lavalink.assert_not_awaited()

    async def test_ytdlp_failure_falls_back_once(self):
        service = self.make_service()
        fallback = Track("lavalink", "https://example.test/yt", None, "user", provider="lavalink", provider_track=object())
        with patch.object(service._ytdlp, "resolve", new=AsyncMock(side_effect=MusicError("blocked"))), \
             patch.object(service._lavalink, "resolve", new=AsyncMock(return_value=[fallback])) as lavalink:
            result = await service.resolve_tracks("never gonna give you up", "user")
        self.assertEqual(result[0].provider, "lavalink")
        lavalink.assert_awaited_once()

    async def test_both_providers_fail_with_friendly_error(self):
        service = self.make_service()
        with patch.object(service._ytdlp, "resolve", new=AsyncMock(side_effect=MusicError("blocked"))), \
             patch.object(service._lavalink, "resolve", new=AsyncMock(side_effect=MusicError("offline"))):
            with self.assertRaises(MusicError) as raised:
                await service.resolve_tracks("query", "user")
        self.assertIn("provedores disponiveis", str(raised.exception))

    async def test_lavalink_disabled_keeps_ytdlp_only(self):
        bot = SimpleNamespace(loop=asyncio.get_running_loop())
        service = MusicService(bot, Settings(discord_token="test", lavalink_enabled=False))
        self.assertEqual(service.health["yt-dlp"], "available")
        self.assertEqual(service.health["lavalink"], "disconnected")
        with patch.object(service._ytdlp, "resolve", new=AsyncMock(return_value=[Track("yt", "url", "stream", "u")])) as ytdlp:
            await service.resolve_tracks("query", "u")
        ytdlp.assert_awaited_once()

    async def test_players_are_independent_per_guild(self):
        service = self.make_service()
        first = service.player_for(1)
        second = service.player_for(2)
        first.add_many([Track("one", "url", "stream", "u")], SimpleNamespace())
        self.assertEqual(first.queue_size(), 1)
        self.assertEqual(second.queue_size(), 0)

    async def test_lavalink_end_uses_same_queue_and_starts_next(self):
        service = self.make_service()
        player = service.player_for(10)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        player._queue.append(Track("two", "url", None, "u", provider="lavalink", provider_track=object()))
        player._play_next_impl = AsyncMock()
        voice = SimpleNamespace()
        await player.handle_lavalink_end(voice)
        player._play_next_impl.assert_awaited_once_with(voice)
        self.assertEqual(len(player.queue_snapshot()), 1)

    async def test_skip_uses_lavalink_player_without_touching_queue(self):
        service = self.make_service()
        player = service.player_for(11)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        player._queue.append(Track("two", "url", None, "u", provider="lavalink", provider_track=object()))
        voice = SimpleNamespace(playing=True, paused=False, skip=AsyncMock())
        with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
            await player.skip(voice)
        voice.skip.assert_awaited_once_with(force=True)
        self.assertEqual(len(player.queue_snapshot()), 1)

    async def test_duplicate_lavalink_events_advance_only_once(self):
        service = self.make_service()
        player = service.player_for(12)
        playable = object()
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=playable)
        player._play_next_impl = AsyncMock()
        voice = SimpleNamespace()
        await player.handle_lavalink_end(voice, event_track=playable)
        await player.handle_lavalink_end(voice, error=RuntimeError("stuck"), event_track=playable)
        player._play_next_impl.assert_awaited_once_with(voice)

    async def test_repeat_lavalink_replays_same_track_without_queue_duplication(self):
        service = self.make_service()
        player = service.player_for(13)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        player.set_repeat(RepeatMode.ONE)
        player._play_current = AsyncMock()
        voice = SimpleNamespace()
        await player._play_next(voice)
        await player._play_next(voice)
        self.assertEqual(player._play_current.await_count, 2)
        self.assertEqual(len(player.queue_snapshot()), 0)

    async def test_lastfm_lavalink_start_and_end_are_not_duplicated(self):
        scrobbler = SimpleNamespace(started=unittest.mock.Mock(), ended=unittest.mock.Mock())
        bot = SimpleNamespace(loop=asyncio.get_running_loop(), _lastfm_scrobbler=scrobbler)
        service = MusicService(bot, Settings(discord_token="test", lavalink_enabled=True, lavalink_password="secret"))
        service._lavalink_connected = True
        player = service.player_for(14)
        playable = object()
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=playable)
        player._play_next_impl = AsyncMock()
        voice = SimpleNamespace(play=AsyncMock(), channel=SimpleNamespace(members=[]))
        with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
            await player._play_current(voice)
            await player.handle_lavalink_end(voice, event_track=playable)
            await player.handle_lavalink_end(voice, event_track=playable)
        self.assertEqual(scrobbler.started.call_count, 1)
        self.assertEqual(scrobbler.ended.call_count, 1)

    async def test_local_ffmpeg_error_does_not_fallback(self):
        service = self.make_service()
        player = service.player_for(15)
        player._current = Track("one", "url", "stream", "u")
        player._play_current = AsyncMock(side_effect=discord.ClientException("ffmpeg was not found"))
        player._queue.clear()
        with patch.object(service, "resolve_with_lavalink", new=AsyncMock()) as fallback:
            await player._play_next(SimpleNamespace())
        fallback.assert_not_awaited()

    async def test_stop_lavalink_clears_state_and_late_event_is_ignored(self):
        service = self.make_service()
        player = service.player_for(16)
        playable = object()
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=playable)
        player._queue.append(Track("two", "url", None, "u", provider="lavalink", provider_track=object()))
        voice = SimpleNamespace(playing=True, paused=False, stop=AsyncMock(), disconnect=AsyncMock())
        with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
            await player.stop(voice)
            await player.handle_lavalink_end(voice, event_track=playable)
        self.assertIsNone(player.current)
        self.assertEqual(player.queue_size(), 0)
        voice.disconnect.assert_awaited_once()

    async def test_stop_ffmpeg_stops_before_disconnect(self):
        service = self.make_service()
        player = service.player_for(161)
        player._current = Track("one", "url", "stream", "u")
        voice = SimpleNamespace(is_playing=lambda: True, is_paused=lambda: False, stop=unittest.mock.Mock(), disconnect=AsyncMock())
        with patch("bot.services.music_player._is_lavalink_voice", return_value=False):
            await player.stop(voice)
        voice.stop.assert_called_once_with()
        voice.disconnect.assert_awaited_once()

    async def test_seek_and_volume_use_lavalink_units(self):
        service = self.make_service()
        player = service.player_for(18)
        player._current = Track("one", "url", None, "u", duration=300, provider="lavalink", provider_track=object())
        voice = SimpleNamespace(playing=True, paused=False, seek=AsyncMock(), set_volume=AsyncMock())
        player._voice_client = voice
        with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
            await player.seek(voice, 42)
            player.set_volume(125)
            await asyncio.sleep(0)
        voice.seek.assert_awaited_once_with(42000)
        voice.set_volume.assert_awaited_once_with(125)
        self.assertEqual(player.current_position(), 42)

    async def test_advanced_filter_does_not_mutate_lavalink_state(self):
        service = self.make_service()
        player = service.player_for(19)
        voice = SimpleNamespace(set_filters=AsyncMock())
        with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
            with self.assertRaises(MusicError):
                await player.set_filter(voice, "nightcore")
        self.assertEqual(player.filter_name, "none")
        voice.set_filters.assert_not_awaited()

    async def test_mixed_provider_queue_keeps_one_logical_sequence(self):
        service = self.make_service()
        player = service.player_for(17)
        tracks = [
            Track("a", "url", "stream", "u", provider="ytdlp"),
            Track("b", "url", None, "u", provider="lavalink", provider_track=object()),
            Track("c", "url", "stream", "u", provider="ytdlp"),
        ]
        player._queue.extend(tracks)
        played = []
        async def play_current(_voice):
            played.append(player.current.title)
        player._play_current = play_current
        clients = [SimpleNamespace(channel=SimpleNamespace(), disconnect=AsyncMock()) for _ in range(3)]
        with patch.object(player, "_ensure_voice_backend", new=AsyncMock(side_effect=clients)) as ensure:
            await player._play_next(SimpleNamespace())
            player._current = tracks[0]
            await player._play_next(clients[0])
            player._current = tracks[1]
            await player._play_next(clients[1])
        self.assertEqual(played, ["a", "b", "c"])
        self.assertEqual(ensure.await_count, 3)

    async def test_lavalink_connection_failure_is_non_fatal_and_can_reconnect(self):
        service = self.make_service()
        service._lavalink_connected = False
        fake_node = SimpleNamespace(close=AsyncMock())
        with patch("bot.services.music_player.wavelink.Node", return_value=fake_node), \
             patch("bot.services.music_player.wavelink.Pool.connect", new=AsyncMock(side_effect=OSError("offline"))) as connect:
            self.assertFalse(await service.connect_lavalink())
        self.assertFalse(service.lavalink_available)
        with patch("bot.services.music_player.wavelink.Node", return_value=fake_node), \
             patch("bot.services.music_player.wavelink.Pool.connect", new=AsyncMock()) as connect:
            self.assertTrue(await service.connect_lavalink())
            connect.assert_awaited_once()
        self.assertTrue(service.lavalink_available)

    async def test_ytdlp_uses_private_writable_cookie_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "cookies.txt")
            with open(source, "w", encoding="utf-8") as cookie_file:
                cookie_file.write("# Netscape HTTP Cookie File\n")
            service = MusicService(
                SimpleNamespace(loop=asyncio.get_running_loop()),
                Settings(discord_token="test", youtube_cookies_path=source),
            )
            options = service._ytdl_options("ytsearch1:test")
            runtime_path = options["cookiefile"]
            self.assertNotEqual(runtime_path, source)
            self.assertTrue(os.path.isfile(runtime_path))
            if os.name != "nt":
                self.assertEqual(os.stat(runtime_path).st_mode & 0o777, 0o600)
            with open(runtime_path, "a", encoding="utf-8") as cookie_file:
                cookie_file.write("# writable\n")

    async def test_lavalink_disconnect_reconnect_cancels_recovery(self):
        service = self.make_service()
        player = service.player_for(30)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        voice = SimpleNamespace(playing=True, paused=False, is_playing=lambda: True, is_paused=lambda: False)
        player._voice_client = voice
        with patch("bot.services.music_player.LAVALINK_RECONNECT_GRACE_SECONDS", 0.02):
            service.handle_lavalink_node_lost("network")
            self.assertIsNotNone(player._lavalink_reconnect_task)
            await service.handle_lavalink_node_ready()
            await asyncio.sleep(0.03)
        self.assertEqual(player.current.provider, "lavalink")

    async def test_lavalink_disconnect_without_reconnect_advances_once(self):
        service = self.make_service()
        service._lavalink_connected = False
        player = service.player_for(31)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        voice = SimpleNamespace(playing=False, paused=False, is_playing=lambda: False, is_paused=lambda: False)
        player._voice_client = voice
        player._play_next_impl = AsyncMock()
        with patch("bot.services.music_player.LAVALINK_RECONNECT_GRACE_SECONDS", 0.01):
            service.handle_lavalink_node_lost("network")
            await asyncio.sleep(0.03)
        player._play_next_impl.assert_awaited_once_with(voice)

    async def test_skip_during_lavalink_recovery_window_cancels_watchdog(self):
        service = self.make_service()
        player = service.player_for(32)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        voice = SimpleNamespace(playing=True, paused=False, skip=AsyncMock())
        player._voice_client = voice
        player._play_next_impl = AsyncMock()
        with patch("bot.services.music_player.LAVALINK_RECONNECT_GRACE_SECONDS", 0.01):
            player.handle_lavalink_connection_lost("network")
            with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
                await player.skip(voice)
            await asyncio.sleep(0.03)
        player._play_next_impl.assert_not_awaited()

    async def test_stop_during_lavalink_recovery_window_cancels_watchdog(self):
        service = self.make_service()
        player = service.player_for(33)
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=object())
        voice = SimpleNamespace(playing=True, paused=False, stop=AsyncMock(), disconnect=AsyncMock())
        player._voice_client = voice
        player._play_next_impl = AsyncMock()
        with patch("bot.services.music_player.LAVALINK_RECONNECT_GRACE_SECONDS", 0.01):
            player.handle_lavalink_connection_lost("network")
            with patch("bot.services.music_player._is_lavalink_voice", return_value=True):
                await player.stop(voice)
            await asyncio.sleep(0.03)
        player._play_next_impl.assert_not_awaited()

    async def test_delayed_lavalink_end_after_recovery_timeout_is_ignored(self):
        service = self.make_service()
        service._lavalink_connected = False
        player = service.player_for(34)
        playable = object()
        player._current = Track("one", "url", None, "u", provider="lavalink", provider_track=playable)
        voice = SimpleNamespace(playing=False, paused=False, is_playing=lambda: False, is_paused=lambda: False)
        player._voice_client = voice
        player._play_next_impl = AsyncMock()
        with patch("bot.services.music_player.LAVALINK_RECONNECT_GRACE_SECONDS", 0.01):
            service.handle_lavalink_node_lost("network")
            await asyncio.sleep(0.03)
            await player.handle_lavalink_end(voice, event_track=playable)
        player._play_next_impl.assert_awaited_once_with(voice)


if __name__ == "__main__":
    unittest.main()
