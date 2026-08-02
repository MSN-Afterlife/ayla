import re

from bot.characters.loader import Character
from bot.config import Settings
from bot.memory.conversation_store import MessageEntry
from bot.services.prompt_builder import build_prompt

try:
    from openai import APIError
    from openai import AsyncOpenAI
    from openai import RateLimitError
except ImportError:
    APIError = None
    AsyncOpenAI = None
    RateLimitError = None


class ChatEngineError(Exception):
    pass


class ChatEngine:
    def __init__(self, character: Character, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("Configure OPENAI_API_KEY no .env para usar o chat com IA.")

        if AsyncOpenAI is None:
            raise RuntimeError("Instale a dependencia openai para usar o chat com IA.")

        self._character = character
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def reply(self, history: list[MessageEntry], user_name: str, message: str) -> str:
        cleaned_message = message.strip()
        if not cleaned_message:
            cleaned_message = "oi"

        prompt = build_prompt(self._character, history, user_name, cleaned_message)
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.openai_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=220,
                presence_penalty=0.4,
                frequency_penalty=0.7,
            )
        except RateLimitError as error:
            if getattr(error, "code", None) == "insufficient_quota":
                raise ChatEngineError("A API da OpenAI esta sem cota/credito. Verifique billing ou troque a chave.") from error

            raise ChatEngineError("A API da OpenAI limitou as respostas por excesso de uso. Tente de novo em instantes.") from error
        except APIError as error:
            raise ChatEngineError("A API da OpenAI retornou erro agora. Tente novamente daqui a pouco.") from error

        content = response.choices[0].message.content
        return _sanitize_reply(content) if content else "Nao sei nem o que te responder agora."


def _sanitize_reply(content: str) -> str:
    cleaned = content.strip()
    cleaned = re.sub(r"^(?:Ayla|Lyn)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bLyn\b", "Ayla", cleaned)
    return cleaned.strip() or "Nao sei nem o que te responder agora."
