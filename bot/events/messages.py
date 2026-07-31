from discord import Message
from discord.ext import commands

from bot.characters.loader import load_character
from bot.config import Settings
from bot.memory.conversation_store import ConversationStore
from bot.services.chat_engine import ChatEngine
from bot.services.chat_engine import ChatEngineError
from bot.services.chat_config import ChatConfigStore
from bot.services.level_service import LevelService


def setup_message_events(bot: commands.Bot, settings: Settings, chat_config: ChatConfigStore) -> None:
    character = load_character(settings.character_file)
    memory = ConversationStore(limit=settings.memory_limit)
    chat_engine = ChatEngine(character, settings)
    level_service = LevelService(settings)

    @bot.event
    async def on_message(message: Message) -> None:
        if message.author.bot:
            return

        await bot.process_commands(message)

        if _is_command_message(message.content, bot.command_prefix):
            return

        if message.guild:
            level_service.add_message_xp(
                guild_id=message.guild.id,
                user_id=message.author.id,
                user_name=message.author.display_name,
            )

        configured_channel_id = chat_config.get_channel_id(message.guild.id) if message.guild else None
        if configured_channel_id and message.channel.id != configured_channel_id:
            return

        mentioned = bool(bot.user and bot.user in message.mentions)
        if not configured_channel_id and not mentioned:
            return

        conversation_id = f"{message.guild.id if message.guild else 'dm'}:{message.channel.id}"
        user_name = message.author.display_name
        content = _clean_bot_mention(message.clean_content, bot.user.display_name if bot.user else "")

        history = memory.get_recent(conversation_id)
        try:
            response = await chat_engine.reply(history, user_name, content)
        except ChatEngineError as error:
            await message.channel.send(str(error))
            return

        memory.add(conversation_id, user_name, content)
        memory.add(conversation_id, character.name, response)

        await message.channel.send(response)


def _is_command_message(content: str, prefixes) -> bool:
    if isinstance(prefixes, str):
        return content.startswith(prefixes)

    return any(content.startswith(prefix) for prefix in prefixes)


def _clean_bot_mention(content: str, bot_name: str) -> str:
    if not bot_name:
        return content.strip()

    return content.replace(f"@{bot_name}", "").strip()
