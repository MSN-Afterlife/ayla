import random

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

        normalized = message.lower().strip()
        casual_greetings = ("oi", "oie", "ola", "olá", "eae", "eaí", "opa", "salve", "bom dia", "boa tarde", "boa noite")
        just_chatting = ("só conversando", "so conversando", "conversando com você", "conversando com voce", "papo", "bater papo")

        if not normalized:
            return random.choice(
                [
                    "To aqui sim. Manda ai.",
                    "Oi, cheguei. Fala comigo.",
                    "Hmm? To ouvindo.",
                ]
            )

        if any(item in normalized for item in casual_greetings):
            return random.choice(
                [
                    "Eae. To bem sim, e voce?",
                    "Opa, to tranquila. Como ce ta?",
                    "Oi oi. To de boa por aqui, e tu?",
                ]
            )

        if any(item in normalized for item in just_chatting):
            return random.choice(
                [
                    "Ah ta, foi mal. Entrei em modo seria por meio segundo. Bora conversar normal entao.",
                    "Justo, justo. Sem palestra entao. Como ta teu dia?",
                    "Ta bom, parei de complicar. Pode falar comigo de boa.",
                ]
            )

        if "como ce ta" in normalized or "como vc ta" in normalized or "tudo bem" in normalized:
            return random.choice(
                [
                    "To bem, de boa. E voce, ta suave?",
                    "To tranquila. Meio com cara de quem acabou de acordar, mas bem.",
                    "Tudo certo por aqui. E contigo?",
                ]
            )

        if message.endswith("?"):
            return random.choice(
                [
                    "Acho que sim, mas depende um pouco do contexto.",
                    "Hmm, boa. Eu diria que sim, mas me da mais um detalhe.",
                    "Do jeito que voce falou, parece que sim. O que aconteceu?",
                ]
            )

        return random.choice(
            [
                "Saquei. E ai, como ficou isso?",
                "Entendi. Isso ai parece assunto com historia por tras.",
                "Aham, to contigo. Continua.",
                "Nossa, do nada assim? Agora fiquei curiosa.",
            ]
        )
