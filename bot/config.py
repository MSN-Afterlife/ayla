import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_token: str
    command_prefix: str = "!"
    character_file: str = "bot/characters/default.json"
    memory_limit: int = 12


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Configure DISCORD_TOKEN no arquivo .env antes de iniciar o bot.")

    return Settings(
        discord_token=token,
        command_prefix=os.getenv("COMMAND_PREFIX", "!"),
        character_file=os.getenv("CHARACTER_FILE", "bot/characters/default.json"),
        memory_limit=int(os.getenv("MEMORY_LIMIT", "12")),
    )
