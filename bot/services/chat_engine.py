from bot.characters.loader import Character
from bot.config import Settings
from bot.memory.conversation_store import MessageEntry
from bot.services.prompt_builder import build_prompt

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


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
        response = await self._client.chat.completions.create(
            model=self._settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.95,
            max_tokens=220,
            presence_penalty=0.4,
            frequency_penalty=0.3,
        )
        content = response.choices[0].message.content
        return content.strip() if content else "Nao sei nem o que te responder agora."
