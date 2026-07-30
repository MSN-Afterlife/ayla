import discord
from discord.ext import commands

from bot.commands.basic import setup_basic_commands
from bot.config import Settings
from bot.events.messages import setup_message_events


def create_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True

    bot = commands.Bot(command_prefix=settings.command_prefix, intents=intents)

    @bot.event
    async def on_ready() -> None:
        print(f"Bot conectado como {bot.user}")

    setup_basic_commands(bot)
    setup_message_events(bot, settings)
    return bot
