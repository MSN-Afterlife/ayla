import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_token: str
    command_prefix: str = "!"
    character_file: str = "bot/characters/default.json"
    memory_limit: int = 12
    gif_provider_order: list[str] | None = None
    klipy_api_key: str | None = None
    klipy_gif_search_url: str = "https://api.klipy.com/api/v1/gifs/search"
    apileague_api_key: str | None = None
    giphy_api_key: str | None = None
    youtube_api_key: str | None = None
    google_search_api_key: str | None = None
    google_search_engine_id: str | None = None


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
        gif_provider_order=_load_list("GIF_PROVIDER_ORDER", ["klipy", "apileague"]),
        klipy_api_key=os.getenv("KLIPY_API_KEY"),
        klipy_gif_search_url=os.getenv("KLIPY_GIF_SEARCH_URL", "https://api.klipy.com/api/v1/gifs/search"),
        apileague_api_key=os.getenv("APILEAGUE_API_KEY"),
        giphy_api_key=os.getenv("GIPHY_API_KEY"),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY"),
        google_search_api_key=os.getenv("GOOGLE_SEARCH_API_KEY"),
        google_search_engine_id=os.getenv("GOOGLE_SEARCH_ENGINE_ID"),
    )


def _load_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default

    return [item.strip().lower() for item in value.split(",") if item.strip()]
