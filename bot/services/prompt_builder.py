from bot.characters.loader import Character
from bot.memory.conversation_store import MessageEntry


def build_prompt(character: Character, history: list[MessageEntry], user_name: str, message: str) -> str:
    personality = "\n".join(f"- {item}" for item in character.personality)
    boundaries = "\n".join(f"- {item}" for item in character.boundaries)
    conversation = "\n".join(f"{entry.author}: {entry.content}" for entry in history)

    return (
        f"Personagem: {character.name}\n"
        f"Descricao: {character.short_description}\n\n"
        f"Personalidade:\n{personality}\n\n"
        f"Limites:\n{boundaries}\n\n"
        f"Historico recente:\n{conversation or 'Sem historico ainda.'}\n\n"
        f"Mensagem atual de {user_name}: {message}\n"
        "Responda como a personagem, em pt-BR, de forma natural e contextual."
    )
