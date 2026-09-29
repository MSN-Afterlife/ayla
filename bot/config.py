import os
import json
from dataclasses import dataclass

from dotenv import load_dotenv

CLICKUP_DESTINATION_KEYS = {
    "ayla_bugs", "site_bugs", "incidents", "security", "suggestions", "community", "manual_triage",
}


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
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
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
    task_ai_model: str = "gpt-5.4-mini"
    task_ai_timeout_seconds: float = 120
    levels_database_path: str = "data/levels.sqlite3"
    economy_write_gate_state_path: str = "data/economy_write_gate.json"
    profile_background_storage_path: str = "data/profile_backgrounds"
    levels_xp_min: int = 15
    levels_xp_max: int = 25
    levels_cooldown_seconds: int = 60
    uno_chaos_deck_size: int = 500
    uno_monsters_path: str = "data/monsters_catalog.json"
    chat_config_path: str = "data/chat_config.json"
    presence_config_path: str = "data/presence_config.json"
    site_api_enabled: bool = True
    site_api_host: str = "0.0.0.0"
    site_api_port: int = 8090
    site_api_cors_origin: str = "*"
    site_api_key: str | None = None
    site_admin_api_key: str | None = None
    ayla_minecraft_internal_token: str | None = None
    migration_engine_base_url: str | None = None
    migration_engine_token: str | None = None
    migration_engine_timeout_seconds: float = 10
    migration_confirmation_ttl_seconds: int = 120
    daily_site_url: str = "https://msnafterlife.online/daily-ayla/"
    daily_direct_claim_enabled: bool = False
    youtube_cookies_path: str | None = None
    youtube_js_runtime_path: str = "node"
    lavalink_enabled: bool = False
    lavalink_host: str = "lavalink"
    lavalink_port: int = 2333
    lavalink_uri: str = "http://lavalink:2333"
    lavalink_password: str | None = None
    lavalink_secure: bool = False
    lavalink_reconnect: bool = True
    lavalink_reconnect_attempts: int = 5
    authentik_url: str | None = None
    authentik_token: str | None = None
    authentik_authdev_group: str | None = None
    ayla_caotica_url: str | None = None
    clickup_api_token: str | None = None
    clickup_list_id: str | None = None
    clickup_allowed_role_ids: list[int] | None = None
    clickup_destinations: dict[str, str] | None = None
    clickup_custom_field_ids: dict[str, str] | None = None
    # Keep the Free plan safe by default: use native fields, tags and markdown.
    clickup_free_mode: bool = True
    clickup_workspace_id: str | None = None
    clickup_catalog_cache_seconds: int = 600
    clickup_manual_triage_query: str = "Backlog > Triage Manual"
    lastfm_api_key: str | None = None
    lastfm_api_secret: str | None = None
    lastfm_callback_url: str | None = None
    lastfm_enabled: bool = False
    lastfm_database_path: str = "data/lastfm.sqlite3"
    lastfm_timeout_seconds: float = 10
    environment: str = "production"


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Configure DISCORD_TOKEN no arquivo .env antes de iniciar o bot.")

    return Settings(
        discord_token=token,
        environment=os.getenv("AYLA_ENV", "production").strip().lower(),
        command_prefix=os.getenv("COMMAND_PREFIX", "a!"),
        character_file=os.getenv("CHARACTER_FILE", "bot/characters/default.json"),
        memory_limit=int(os.getenv("MEMORY_LIMIT", "12")),
        gif_provider_order=_load_list("GIF_PROVIDER_ORDER", ["klipy", "apileague"]),
        klipy_api_key=os.getenv("KLIPY_API_KEY"),
        klipy_gif_search_url=os.getenv("KLIPY_GIF_SEARCH_URL", "https://api.klipy.com/api/v1/gifs/search"),
        apileague_api_key=os.getenv("APILEAGUE_API_KEY"),
        giphy_api_key=os.getenv("GIPHY_API_KEY"),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY"),
        spotify_client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
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
        task_ai_model=os.getenv("TASK_AI_MODEL", "gpt-5.4-mini"),
        task_ai_timeout_seconds=float(os.getenv("TASK_AI_TIMEOUT_SECONDS", "120")),
        levels_database_path=os.getenv("LEVELS_DATABASE_PATH", "data/levels.sqlite3"),
        economy_write_gate_state_path=os.getenv("ECONOMY_WRITE_GATE_STATE_PATH", "data/economy_write_gate.json"),
        profile_background_storage_path=os.getenv("PROFILE_BACKGROUND_STORAGE_PATH", "data/profile_backgrounds"),
        levels_xp_min=int(os.getenv("LEVELS_XP_MIN", "15")),
        levels_xp_max=int(os.getenv("LEVELS_XP_MAX", "25")),
        levels_cooldown_seconds=int(os.getenv("LEVELS_COOLDOWN_SECONDS", "60")),
        uno_chaos_deck_size=min(1_000_000, max(108, int(os.getenv("UNO_CHAOS_DECK_SIZE", "500")))),
        uno_monsters_path=os.getenv("UNO_MONSTERS_PATH", "data/monsters_catalog.json"),
        chat_config_path=os.getenv("CHAT_CONFIG_PATH", "data/chat_config.json"),
        presence_config_path=os.getenv("PRESENCE_CONFIG_PATH", "data/presence_config.json"),
        site_api_enabled=_load_bool("SITE_API_ENABLED", True),
        site_api_host=os.getenv("SITE_API_HOST", "0.0.0.0"),
        site_api_port=int(os.getenv("SITE_API_PORT", os.getenv("PORT", "8090"))),
        site_api_cors_origin=os.getenv("SITE_API_CORS_ORIGIN", "*"),
        site_api_key=os.getenv("SITE_API_KEY"),
        site_admin_api_key=os.getenv("SITE_ADMIN_API_KEY"),
        ayla_minecraft_internal_token=os.getenv("AYLA_MINECRAFT_INTERNAL_TOKEN"),
        migration_engine_base_url=os.getenv("MIGRATION_ENGINE_BASE_URL"),
        migration_engine_token=os.getenv("MIGRATION_ENGINE_TOKEN"),
        migration_engine_timeout_seconds=float(os.getenv("MIGRATION_ENGINE_TIMEOUT_SECONDS", "10")),
        migration_confirmation_ttl_seconds=int(os.getenv("MIGRATION_CONFIRMATION_TTL_SECONDS", "120")),
        daily_site_url=os.getenv("DAILY_SITE_URL", "https://msnafterlife.online/daily-ayla/"),
        daily_direct_claim_enabled=_load_bool("DAILY_DIRECT_CLAIM_ENABLED", False),
        youtube_cookies_path=os.getenv("YOUTUBE_COOKIES_PATH"),
        youtube_js_runtime_path=os.getenv("YOUTUBE_JS_RUNTIME_PATH", "node"),
        lavalink_enabled=_load_bool("LAVALINK_ENABLED", False),
        lavalink_host=os.getenv("LAVALINK_HOST", "lavalink"),
        lavalink_port=int(os.getenv("LAVALINK_PORT", "2333")),
        lavalink_uri=os.getenv("LAVALINK_URI", f"{'https' if _load_bool('LAVALINK_SECURE', False) else 'http'}://{os.getenv('LAVALINK_HOST', 'lavalink')}:{os.getenv('LAVALINK_PORT', '2333')}"),
        lavalink_password=os.getenv("LAVALINK_PASSWORD"),
        lavalink_secure=_load_bool("LAVALINK_SECURE", False),
        lavalink_reconnect=_load_bool("LAVALINK_RECONNECT", True),
        lavalink_reconnect_attempts=max(0, int(os.getenv("LAVALINK_RECONNECT_ATTEMPTS", "5"))),
        authentik_url=os.getenv("AUTHENTIK_URL"),
        authentik_token=os.getenv("AUTHENTIK_TOKEN"),
        authentik_authdev_group=os.getenv("AUTHENTIK_AUTHDEV_GROUP"),
        ayla_caotica_url=os.getenv("AYLACAOTICAURL"),
        clickup_api_token=os.getenv("CLICKUP_API_TOKEN"),
        clickup_list_id=os.getenv("CLICKUP_LIST_ID"),
        clickup_allowed_role_ids=_load_int_list("CLICKUP_ALLOWED_ROLE_IDS"),
        clickup_destinations=_load_destination_map("CLICKUP_DESTINATIONS"),
        clickup_custom_field_ids=_load_json_map("CLICKUP_CUSTOM_FIELD_IDS"),
        clickup_free_mode=_load_bool("CLICKUP_FREE_MODE", True),
        clickup_workspace_id=os.getenv("CLICKUP_WORKSPACE_ID"),
        clickup_catalog_cache_seconds=int(os.getenv("CLICKUP_CATALOG_CACHE_SECONDS", "600")),
        clickup_manual_triage_query=os.getenv("CLICKUP_MANUAL_TRIAGE_QUERY", "Backlog > Triage Manual"),
        lastfm_api_key=os.getenv("LASTFM_API_KEY"),
        lastfm_api_secret=os.getenv("LASTFM_API_SECRET"),
        lastfm_callback_url=os.getenv("LASTFM_CALLBACK_URL"),
        lastfm_enabled=_load_bool("LASTFM_ENABLED", True),
        lastfm_database_path=os.getenv("LASTFM_DATABASE_PATH", "data/lastfm.sqlite3"),
        lastfm_timeout_seconds=float(os.getenv("LASTFM_TIMEOUT_SECONDS", "10")),
    )


def validate_clickup_settings(settings: Settings) -> None:
    if not settings.clickup_api_token:
        return
    destinations = settings.clickup_destinations or {}
    if not destinations:
        if settings.clickup_list_id:
            return
        raise RuntimeError(
            "CLICKUP_DESTINATIONS não está configurado. Preencha as sete chaves "
            "ayla_bugs, site_bugs, incidents, security, suggestions, community e manual_triage."
        )
    missing = set()
    unknown = set(destinations) - CLICKUP_DESTINATION_KEYS
    empty = [key for key in CLICKUP_DESTINATION_KEYS if not str(destinations.get(key, "")).strip()]
    if unknown or empty:
        raise RuntimeError(
            f"CLICKUP_DESTINATIONS inválido: missing={sorted(missing)} unknown={sorted(unknown)} empty={sorted(empty)}."
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


def _load_int_list(name: str) -> list[int]:
    value = os.getenv(name)
    if not value:
        return []

    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            raise RuntimeError(f"{name} deve conter apenas IDs numericos separados por virgula.") from None
    return result

def _load_json_map(name: str) -> dict[str, str]:
    value = os.getenv(name)
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{name} deve conter um objeto JSON.") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{name} deve conter um objeto JSON.")
    result: dict[str, str] = {}
    for key, item in payload.items():
        if not str(key).strip() or not str(item).strip():
            raise RuntimeError(f"{name} contém uma chave ou list_id vazio: {key!r}.")
        result[str(key)] = str(item)
    return result


def _load_destination_map(name: str) -> dict[str, str]:
    result = _load_json_map(name)
    if not result:
        return {}
    unknown = set(result) - CLICKUP_DESTINATION_KEYS
    missing = CLICKUP_DESTINATION_KEYS - set(result)
    if unknown:
        raise RuntimeError(f"{name} contém destinos desconhecidos: {sorted(unknown)}.")
    if missing:
        raise RuntimeError(f"{name} não contém list_id para: {sorted(missing)}.")
    return result
