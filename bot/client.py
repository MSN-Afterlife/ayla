import discord
from discord.ext import commands

from bot.commands.basic import setup_basic_commands
from bot.commands.interactions import setup_interaction_commands
from bot.commands.media import setup_media_commands
from bot.config import Settings
from bot.events.messages import setup_message_events


def create_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    prefixes = [settings.command_prefix]
    if "+" not in prefixes:
        prefixes.append("+")

    bot = commands.Bot(command_prefix=prefixes, intents=intents)

    @bot.event
    async def on_ready() -> None:
        print(f"Bot conectado como {bot.user}")

    setup_basic_commands(bot)
    setup_media_commands(bot, settings)
    setup_interaction_commands(bot, settings)
    setup_message_events(bot, settings)
    return bot
