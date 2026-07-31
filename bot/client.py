import discord
from discord.ext import commands

from bot.commands.basic import setup_basic_commands
from bot.commands.chat_config import setup_chat_config_commands
from bot.commands.help import setup_help_command
from bot.commands.interactions import setup_interaction_commands
from bot.commands.levels import setup_level_commands
from bot.commands.media import setup_media_commands
from bot.config import Settings
from bot.events.messages import setup_message_events
from bot.services.chat_config import ChatConfigStore


def create_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    prefixes = [settings.command_prefix]
    if "+" not in prefixes:
        prefixes.append("+")

    bot = commands.Bot(command_prefix=prefixes, intents=intents, help_command=None)
    bot._slash_synced = False
    chat_config = ChatConfigStore(settings.chat_config_path)

    @bot.event
    async def on_ready() -> None:
        if not bot._slash_synced:
            synced = await bot.tree.sync()
            bot._slash_synced = True
            print(f"{len(synced)} comandos slash sincronizados.")

        print(f"Bot conectado como {bot.user}")

    setup_basic_commands(bot)
    setup_media_commands(bot, settings)
    setup_interaction_commands(bot, settings)
    setup_level_commands(bot, settings)
    setup_chat_config_commands(bot, chat_config)
    setup_help_command(bot, settings)
    setup_message_events(bot, settings, chat_config)
    return bot
