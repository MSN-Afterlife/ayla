import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import utcnow

from bot.commands.basic import setup_basic_commands
from bot.commands.clickup import setup_clickup_commands
from bot.commands.announcements import setup_announcement_commands
from bot.commands.authdev import setup_authdev_commands
from bot.commands.chat_config import setup_chat_config_commands
from bot.commands.economy import setup_economy_commands
from bot.commands.help import setup_help_command
from bot.commands.interactions import setup_interaction_commands
from bot.commands.levels import setup_level_commands
from bot.commands.media import setup_media_commands
from bot.commands.music import setup_music_commands
from bot.commands.presence import setup_presence_commands
from bot.commands.presence import start_presence_rotation
from bot.commands.uno import setup_uno_commands
from bot.commands.bingo import setup_bingo_commands
from bot.config import Settings
from bot.command_debug import setup_command_debug
from bot.events.messages import setup_message_events
from bot.services.chat_config import ChatConfigStore
from bot.services.site_api import SiteApiServer
from bot.services.lastfm_service import LastFmService
from bot.services.lastfm_scrobble import LastFmScrobbler
from bot.commands.lastfm import setup_lastfm_commands


class AylaBot(commands.Bot):
    def __init__(self, settings: Settings, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lastfm = LastFmService(settings)
        self._lastfm_scrobbler = LastFmScrobbler(self._lastfm)
        self._site_api = SiteApiServer(settings, self.is_ready, self._lastfm) if settings.site_api_enabled else None

    async def setup_hook(self) -> None:
        if self._site_api:
            await self._site_api.start()
        music_service = getattr(self, "_music_service", None)
        if music_service:
            music_service.prepare_youtube_cookies()
            await music_service.connect_lavalink()
        self.loop.create_task(start_presence_rotation(self))

    async def close(self) -> None:
        if self._site_api:
            await self._site_api.stop()
        clickup_service = getattr(self, "_clickup_service", None)
        if clickup_service:
            await clickup_service.close()
        task_ai_service = getattr(self, "_task_ai_service", None)
        if task_ai_service:
            await task_ai_service.close()
        await self._lastfm.close()
        await super().close()

    async def on_wavelink_track_end(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service:
            await music_service.handle_lavalink_end(payload.player, event_track=payload.track)

    async def on_wavelink_track_start(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service and payload.player:
            await music_service.handle_lavalink_start(payload.player, payload.track)

    async def on_wavelink_node_ready(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service:
            await music_service.handle_lavalink_node_ready(payload.resumed)

    async def on_wavelink_node_closed(self, node, disconnected) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service:
            music_service.handle_lavalink_node_lost("node closed")

    async def on_wavelink_node_disconnected(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service:
            music_service.handle_lavalink_node_lost("node disconnected")

    async def on_wavelink_track_exception(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service:
            await music_service.handle_lavalink_end(payload.player, error=payload.exception, event_track=payload.track)

    async def on_wavelink_track_stuck(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service:
            await music_service.handle_lavalink_end(payload.player, error=RuntimeError("Lavalink track stuck"), event_track=payload.track)

    async def on_wavelink_websocket_closed(self, payload) -> None:
        music_service = getattr(self, "_music_service", None)
        if music_service and payload.player:
            await music_service.handle_lavalink_voice_closed(payload.player, payload.reason)


def create_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = AylaBot(settings, command_prefix=settings.command_prefix, intents=intents, help_command=None)
    bot._slash_synced = False
    bot.started_at = utcnow()
    chat_config = ChatConfigStore(settings.chat_config_path)

    @bot.event
    async def on_ready() -> None:
        if not bot._slash_synced:
            synced = await _sync_slash_commands(bot)
            bot._slash_synced = True
            names = ", ".join(command.name for command in synced)
            print(f"{len(synced)} comandos slash sincronizados: {names}")

        print(f"Bot conectado como {bot.user}")

    setup_basic_commands(bot)
    bot._clickup_service = setup_clickup_commands(bot, settings)
    setup_announcement_commands(bot)
    setup_authdev_commands(bot, settings)
    setup_media_commands(bot, settings)
    setup_music_commands(bot, settings)
    setup_lastfm_commands(bot, bot._lastfm)
    setup_interaction_commands(bot, settings)
    setup_level_commands(bot, settings)
    setup_chat_config_commands(bot, chat_config)
    setup_economy_commands(bot, settings)
    setup_uno_commands(bot, settings)
    setup_bingo_commands(bot)
    setup_help_command(bot, settings)
    setup_message_events(bot, settings, chat_config)
    setup_command_debug(bot)
    return bot


async def _sync_slash_commands(bot: commands.Bot) -> list[app_commands.AppCommand]:
    local_commands = bot.tree.get_commands()
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    for command in local_commands:
        bot.tree.add_command(command)

    if bot.guilds:
        synced: list[app_commands.AppCommand] = []
        for guild in bot.guilds:
            guild_object = discord.Object(id=guild.id)
            bot.tree.copy_global_to(guild=guild_object)
            guild_synced = await bot.tree.sync(guild=guild_object)
            synced.extend(guild_synced)
            print(f"{len(guild_synced)} comandos slash sincronizados no servidor {guild.name} ({guild.id}).")

        return synced

    return await bot.tree.sync()
