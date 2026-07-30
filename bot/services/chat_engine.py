from bot.characters.loader import Character
from bot.memory.conversation_store import MessageEntry
from bot.services.prompt_builder import build_prompt


class ChatEngine:
    def __init__(self, character: Character) -> None:
        self._character = character

    async def reply(self, history: list[MessageEntry], user_name: str, message: str) -> str:
        cleaned_message = message.strip()
        if not cleaned_message:
            return self._character.fallbacks.get("empty_message", "Estou aqui.")

        prompt = build_prompt(self._character, history, user_name, cleaned_message)
        return self._local_character_reply(prompt, cleaned_message)

    def _local_character_reply(self, prompt: str, message: str) -> str:
        del prompt

        if message.endswith("?"):
            return (
                "Boa pergunta. Pelo que voce trouxe, eu pensaria nisso por partes: "
                f"primeiro entender melhor o que voce quer com \"{message}\" e depois responder sem atropelar o contexto."
            )

        return (
            f"Entendi. Quando voce diz \"{message}\", eu fico com a sensacao de que tem algo interessante ai. "
            "Me conta um pouco mais para eu continuar no mesmo clima."
        )
