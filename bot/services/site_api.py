import json
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
from bot.services.economy_service import EconomyService


DEFAULT_CHAOS_MONSTERS = {
    "godzilla": ("Godzilla", ["rampage", "roar", "stomp", "atomic", "tailwind"]),
    "kraken": ("Kraken", ["tentacles", "drown", "gift", "tide", "steal"]),
    "dragao": ("Dragao", ["fire", "hoard", "flight", "scales", "burn"]),
    "mothra": ("Mothra", ["dust", "blessing", "flutter", "heal", "swarm"]),
    "minotauro": ("Minotauro", ["charge", "labyrinth", "axe", "rage", "guard"]),
    "medusa": ("Medusa", ["petrify", "gaze", "snakes", "curse", "mirror"]),
    "yeti": ("Yeti", ["blizzard", "snowball", "warmth", "freeze", "avalanche"]),
    "fenix": ("Fenix", ["rebirth", "flames", "ashes", "sun", "spark"]),
    "cthulhu": ("Cthulhu", ["madness", "whispers", "void", "dream", "tentacles"]),
    "king_kong": ("King Kong", ["smash", "roar", "climb", "protect", "throw"]),
    "slime": ("Slime Mutante", ["split", "absorb", "bounce", "melt", "clone"]),
    "robo_caos": ("Robo Caos", ["hack", "laser", "repair", "overload", "shuffle"]),
    "ayla_caotica": ("Ayla Caotica", ["roulette", "favor", "prank", "glitch", "gift"]),
}


class SiteApiServer:
    def __init__(self, settings: Settings, readiness_check: Callable[[], bool]) -> None:
        self._settings = settings
        self._readiness_check = readiness_check
        self._economy = EconomyService(settings)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if self._runner:
            return

        app = web.Application(middlewares=[_cors_middleware(self._settings.site_api_cors_origin)])
        app.router.add_get("/health", self._health)
        app.router.add_get("/api/site", self._site_info)
        app.router.add_get("/api/uno/monsters", self._monsters)
        app.router.add_get("/api/uno/monsters/schema", self._monsters_schema)
        app.router.add_get("/api/admin/uno/monsters", self._monsters)
        app.router.add_get("/api/admin/uno/monsters/schema", self._monsters_schema)
        app.router.add_post("/api/admin/uno/monsters", self._create_monster)
        app.router.add_put("/api/admin/uno/monsters/{monster_id}", self._update_monster)
        app.router.add_delete("/api/admin/uno/monsters/{monster_id}", self._delete_monster)
        app.router.add_post("/api/admin/uno/monsters/{monster_id}/image", self._upload_monster_image)
        app.router.add_get("/api/uno/monsters/assets/{filename}", self._monster_asset)
        app.router.add_get("/api/admin/uno/monsters/assets/{filename}", self._monster_asset)
        app.router.add_post("/api/daily-ayla", self._daily)
        app.router.add_options("/api/daily-ayla", self._options)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._settings.site_api_host, self._settings.site_api_port)
        await self._site.start()
        print(f"API do site ativa em {self._settings.site_api_host}:{self._settings.site_api_port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _site_info(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "ayla-bot", "dailyRoute": "/api/daily-ayla"})

    async def _monsters(self, request: web.Request) -> web.Response:
        return web.json_response({"monsters": self._load_monsters()})

    def _monster_catalog_path(self) -> Path:
        return Path(self._settings.uno_monsters_path)

    def _load_monsters(self) -> list[dict]:
        path = self._monster_catalog_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
        monsters = [
            {"id": monster_id, "name": name, "image": f"/api/uno/monsters/assets/{monster_id}.png",
             "exists": True, "appearanceChance": 100, "behavior": "mixed", "trigger": "on_appear",
             "actions": [{"type": "chaos_effect", "target": "random_player", "params": {"effects": effects}}],
             "metadata": {"effects": effects}}
            for monster_id, (name, effects) in DEFAULT_CHAOS_MONSTERS.items()
        ]
        self._save_monsters(monsters)
        return monsters

    def _save_monsters(self, monsters: list[dict]) -> None:
        path = self._monster_catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(monsters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    async def _create_monster(self, request: web.Request) -> web.Response:
        try:
            monster = _validate_monster(await request.json())
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)
        monsters = self._load_monsters()
        if any(item.get("id") == monster["id"] for item in monsters):
            return web.json_response({"ok": False, "message": "Monstro ja existe."}, status=409)
        monsters.append(monster)
        self._save_monsters(monsters)
        return web.json_response(monster, status=201)

    async def _update_monster(self, request: web.Request) -> web.Response:
        monster_id = request.match_info["monster_id"]
        try:
            monster = _validate_monster(await request.json(), expected_id=monster_id)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)
        monsters = self._load_monsters()
        for index, current in enumerate(monsters):
            if current.get("id") == monster_id:
                monsters[index] = monster
                self._save_monsters(monsters)
                return web.json_response(monster)
        return web.json_response({"ok": False, "message": "Monstro nao encontrado."}, status=404)

    async def _delete_monster(self, request: web.Request) -> web.Response:
        monster_id = request.match_info["monster_id"]
        monsters = self._load_monsters()
        remaining = [item for item in monsters if item.get("id") != monster_id]
        if len(remaining) == len(monsters):
            return web.json_response({"ok": False, "message": "Monstro nao encontrado."}, status=404)
        self._save_monsters(remaining)
        return web.json_response({"ok": True, "id": monster_id})

    async def _upload_monster_image(self, request: web.Request) -> web.Response:
        monster_id = request.match_info["monster_id"]
        monsters = self._load_monsters()
        if not any(item.get("id") == monster_id for item in monsters):
            return web.json_response({"ok": False, "message": "Monstro nao encontrado."}, status=404)
        try:
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != "file":
                raise ValueError("Envie o arquivo no campo 'file'.")
            extension = Path(field.filename or "").suffix.lower()
            if extension not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".mov"}:
                raise ValueError("Formato de mídia não permitido.")
            data = await field.read(decode=False)
            if not data or len(data) > 10 * 1024 * 1024:
                raise ValueError("O arquivo deve ter entre 1 byte e 10 MB.")
        except (ValueError, web.HTTPException) as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)
        asset_dir = self._monster_catalog_path().parent / "uno_monsters"
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / f"{monster_id}{extension}"
        target.write_bytes(data)
        for old in asset_dir.glob(f"{monster_id}.*"):
            if old != target:
                old.unlink(missing_ok=True)
        for item in monsters:
            if item.get("id") == monster_id:
                item["image"] = f"/api/uno/monsters/assets/{target.name}"
        self._save_monsters(monsters)
        return web.json_response({"ok": True, "image": f"/api/uno/monsters/assets/{target.name}"})

    async def _monster_asset(self, request: web.Request) -> web.StreamResponse:
        filename = Path(request.match_info["filename"]).name
        if filename != request.match_info["filename"] or not re.fullmatch(r"[a-zA-Z0-9_-]+\.(png|jpg|jpeg|gif|webp|mp4|webm|mov)", filename):
            raise web.HTTPNotFound()
        path = self._monster_catalog_path().parent / "uno_monsters" / filename
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Content-Type": mimetypes.guess_type(filename)[0] or "application/octet-stream"})

    async def _monsters_schema(self, request: web.Request) -> web.Response:
        return web.json_response({
            "actionTypes": ["chaos_effect", "draw", "skip", "set_color", "transform", "custom"],
            "targets": ["current_player", "random_player", "all_players", "player_with_most_cards"],
            "behaviors": ["mixed", "aggressive", "supportive", "chaotic"],
            "triggers": ["on_appear", "on_turn", "on_draw", "on_play"],
            "actionFields": {"chaos_effect": ["target", "params"], "draw": ["target", "amount"],
                              "skip": ["target"], "set_color": ["target", "color"],
                              "transform": ["target", "amount"], "custom": ["target", "params"]},
        })

    async def _health(self, request: web.Request) -> web.Response:
        if not self._readiness_check():
            return web.json_response({"ok": False, "status": "starting"}, status=503)
        return web.json_response({"ok": True, "status": "ready"})

    async def _daily(self, request: web.Request) -> web.Response:
        if self._settings.site_api_key:
            sent_key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if sent_key != self._settings.site_api_key:
                return web.json_response({"ok": False, "message": "Nao autorizado."}, status=401)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "message": "JSON invalido."}, status=400)

        raw_user_id = payload.get("discordUserId") or payload.get("userId") or payload.get("discord_user_id")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "message": "discordUserId invalido."}, status=400)

        claim = self._economy.claim_daily(user_id)
        profile = claim.profile
        remaining = claim.remaining_seconds
        if remaining:
            return web.json_response(
                {
                    "ok": False,
                    "message": f"Voce ja pegou o daily hoje. Tente de novo em {_format_remaining(remaining)}.",
                    "balance": profile.balance,
                    "dailyStreak": profile.daily_streak,
                    "remainingSeconds": remaining,
                },
                status=200,
            )

        return web.json_response(
                {
                    "ok": True,
                    "message": "Daily resgatado com sucesso.",
                    "amount": claim.amount or DAILY_AMOUNT,
                    "bonus": claim.bonus,
                    "balance": profile.balance,
                    "dailyStreak": profile.daily_streak,
                }
        )

    async def _options(self, request: web.Request) -> web.Response:
        return web.Response(status=204)


def _cors_middleware(origin: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "content-type, authorization, x-api-key"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    return middleware


def _format_remaining(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


def _validate_monster(payload: object, *, expected_id: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("O corpo deve ser um objeto JSON.")
    monster_id = str(payload.get("id", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", monster_id):
        raise ValueError("ID de monstro inválido.")
    if expected_id is not None and monster_id != expected_id:
        raise ValueError("O ID não pode ser alterado.")
    name = str(payload.get("name", "")).strip()
    if not name or len(name) > 100:
        raise ValueError("Nome de monstro inválido.")
    chance = payload.get("appearanceChance", 100)
    if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not 0 <= chance <= 100:
        raise ValueError("appearanceChance deve ficar entre 0 e 100.")
    actions = payload.get("actions", [])
    if not isinstance(actions, list) or not all(isinstance(action, dict) for action in actions):
        raise ValueError("actions deve ser uma lista de objetos.")
    return {"id": monster_id, "name": name, "image": str(payload.get("image") or f"/api/uno/monsters/assets/{monster_id}.png"),
            "exists": payload.get("exists") is not False, "appearanceChance": int(chance),
            "behavior": str(payload.get("behavior") or "mixed"), "trigger": str(payload.get("trigger") or "on_appear"),
            "actions": actions, "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}}
