import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    discord_token: str
    command_prefix: str = "a!"
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
    image_provider_order: list[str] | None = None
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    unsplash_access_key: str | None = None
    flickr_api_key: str | None = None
    wallhaven_api_key: str | None = None
    brave_search_api_key: str | None = None
    serpapi_api_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    levels_database_path: str = "data/levels.sqlite3"
    levels_xp_min: int = 15
    levels_xp_max: int = 25
    levels_cooldown_seconds: int = 60
    chat_config_path: str = "data/chat_config.json"
    presence_config_path: str = "data/presence_config.json"
    site_api_enabled: bool = True
    site_api_host: str = "0.0.0.0"
    site_api_port: int = 8090
    site_api_cors_origin: str = "*"
    site_api_key: str | None = None
    daily_site_url: str = "https://msnafterlife.online/daily"
    daily_direct_claim_enabled: bool = False
    youtube_cookies_path: str | None = None
    youtube_js_runtime_path: str = "node"
    authentik_url: str | None = None
    authentik_token: str | None = None
    authentik_authdev_group: str | None = None


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Configure DISCORD_TOKEN no arquivo .env antes de iniciar o bot.")

    return Settings(
        discord_token=token,
        command_prefix=os.getenv("COMMAND_PREFIX", "a!"),
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
        image_provider_order=_load_list(
            "IMAGE_PROVIDER_ORDER",
            ["ddgs", "google", "openverse", "wallhaven", "wikimedia", "pexels", "pixabay", "unsplash", "flickr", "brave", "serpapi"],
        ),
        pexels_api_key=os.getenv("PEXELS_API_KEY"),
        pixabay_api_key=os.getenv("PIXABAY_API_KEY"),
        unsplash_access_key=os.getenv("UNSPLASH_ACCESS_KEY"),
        flickr_api_key=os.getenv("FLICKR_API_KEY"),
        wallhaven_api_key=os.getenv("WALLHAVEN_API_KEY"),
        brave_search_api_key=os.getenv("BRAVE_SEARCH_API_KEY"),
        serpapi_api_key=os.getenv("SERPAPI_API_KEY"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        levels_database_path=os.getenv("LEVELS_DATABASE_PATH", "data/levels.sqlite3"),
        levels_xp_min=int(os.getenv("LEVELS_XP_MIN", "15")),
        levels_xp_max=int(os.getenv("LEVELS_XP_MAX", "25")),
        levels_cooldown_seconds=int(os.getenv("LEVELS_COOLDOWN_SECONDS", "60")),
        chat_config_path=os.getenv("CHAT_CONFIG_PATH", "data/chat_config.json"),
        presence_config_path=os.getenv("PRESENCE_CONFIG_PATH", "data/presence_config.json"),
        site_api_enabled=_load_bool("SITE_API_ENABLED", True),
        site_api_host=os.getenv("SITE_API_HOST", "0.0.0.0"),
        site_api_port=int(os.getenv("SITE_API_PORT", os.getenv("PORT", "8090"))),
        site_api_cors_origin=os.getenv("SITE_API_CORS_ORIGIN", "*"),
        site_api_key=os.getenv("SITE_API_KEY"),
        daily_site_url=os.getenv("DAILY_SITE_URL", "https://msnafterlife.online/daily"),
        daily_direct_claim_enabled=_load_bool("DAILY_DIRECT_CLAIM_ENABLED", False),
        youtube_cookies_path=os.getenv("YOUTUBE_COOKIES_PATH"),
        youtube_js_runtime_path=os.getenv("YOUTUBE_JS_RUNTIME_PATH", "node"),
        authentik_url=os.getenv("AUTHENTIK_URL"),
        authentik_token=os.getenv("AUTHENTIK_TOKEN"),
        authentik_authdev_group=os.getenv("AUTHENTIK_AUTHDEV_GROUP"),
    )


def _load_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default

    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _load_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "sim", "on"}
