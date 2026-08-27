import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Meu Bot Site API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=[],
)


def public_site_data() -> dict:
    channels = []
    raw_channels = os.getenv("BOT_CHANNELS_JSON", "[]")
    try:
        import json
        channels = json.loads(raw_channels)
    except Exception:
        pass
    return {
        "server": {
            "name": os.getenv("SERVER_NAME", "MSN Afterlife"),
            "description": os.getenv("SERVER_DESCRIPTION", "Comunidade MSN Afterlife no Discord"),
            "iconUrl": os.getenv("SERVER_ICON_URL") or None,
        },
        "bot": {
            "status": os.getenv("BOT_STATUS", "online"),
            "avatarUrl": os.getenv("BOT_AVATAR_URL") or None,
        },
        "stats": {
            "members": int(os.getenv("SERVER_MEMBERS", "0")),
            "online": int(os.getenv("SERVER_ONLINE", "0")),
        },
        "channels": channels,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/site")
def site() -> dict:
    return public_site_data()
