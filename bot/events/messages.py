from discord import Message
from discord.ext import commands

from bot.characters.loader import load_character
from bot.config import Settings
from bot.memory.conversation_store import ConversationStore
from bot.services.chat_engine import ChatEngine


def setup_message_events(bot: commands.Bot, settings: Settings) -> None:
    character = load_character(settings.character_file)
    memory = ConversationStore(limit=settings.memory_limit)
    chat_engine = ChatEngine(character)

    @bot.event
    async def on_message(message: Message) -> None:
        if message.author.bot:
            return

        await bot.process_commands(message)

        if message.content.startswith(settings.command_prefix):
            return

        if bot.user and bot.user not in message.mentions:
            return

        conversation_id = f"{message.guild.id if message.guild else 'dm'}:{message.channel.id}"
        user_name = message.author.display_name
        content = message.clean_content.replace(f"@{bot.user.display_name}", "").strip() if bot.user else message.clean_content

        history = memory.get_recent(conversation_id)
        response = await chat_engine.reply(history, user_name, content)

        memory.add(conversation_id, user_name, content)
        memory.add(conversation_id, character.name, response)

        await message.channel.send(response)
