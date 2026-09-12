import asyncio
import hmac
import json
import logging
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path

from aiohttp import web

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
from bot.services.economy_service import EconomyService
from bot.services.lastfm_service import LastFmError, LastFmService, safe_page
from bot.services.minecraft_identity import MinecraftIdentityStore, MinecraftLinkError, valid_internal_token
from bot.commands.uno import CHAOS_CHARACTERS

logger = logging.getLogger(__name__)
API_SCOPE_NAMES = ("public", "site-user", "site-admin", "minecraft", "migration")


def api_scope_for(path: str) -> str | None:
    if path.startswith("/api/admin/uno/monsters"):
        return "site-admin"
    if path == "/api/daily-ayla":
        return "site-user"
    if path.startswith("/internal/minecraft/"):
        return "minecraft"
    if path in {"/health", "/api/site", "/auth/lastfm/callback"} or path.startswith("/api/uno/monsters"):
        return "public"
    return None


def _bearer_matches(request: web.Request, expected_token: str | None) -> bool:
    if not expected_token:
        return False
    scheme, separator, token = request.headers.get("Authorization", "").partition(" ")
    return bool(separator and scheme.lower() == "bearer" and hmac.compare_digest(
        token.strip().encode("utf-8"), expected_token.encode("utf-8")
    ))


def _auth_failure_response(request: web.Request, expected_token: str | None, *, missing_status: int = 401) -> web.Response | None:
    if _bearer_matches(request, expected_token):
        return None
    return web.json_response(
        {"ok": False, "error": "unauthorized"},
        status=missing_status if not expected_token else 401,
        headers={"WWW-Authenticate": "Bearer"},
    )


class SiteApiServer:
    def __init__(
        self,
        settings: Settings,
        readiness_check: Callable[[], bool],
        lastfm: LastFmService | None = None,
        minecraft_store: MinecraftIdentityStore | None = None,
    ) -> None:
        self._settings = settings
        self._readiness_check = readiness_check
        self._economy = EconomyService(settings)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._lastfm = lastfm
        self._minecraft = minecraft_store or MinecraftIdentityStore(settings)

    async def start(self) -> None:
        if self._runner:
            return

        app = web.Application(
            middlewares=[_cors_middleware(self._settings.site_api_cors_origin), _api_security_middleware(self._settings)],
            client_max_size=11 * 1024 * 1024,
        )
        app.router.add_get("/health", self._health)
        app.router.add_get("/api/site", self._site_info)
        # These routes are consumed by the UNO Monsters admin page.  Keep the
        # admin aliases here because the public site proxies /api/* to the bot.
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
        app.router.add_get("/auth/lastfm/callback", self._lastfm_callback)
        app.router.add_post("/internal/minecraft/link/request", self._minecraft_link_request)
        app.router.add_get("/internal/minecraft/account/{platform}/{external_id}", self._minecraft_account_lookup)
        app.router.add_get("/internal/minecraft/account/java/{external_id}", self._minecraft_java_lookup)

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
        if self._lastfm:
            await self._lastfm.close()

    async def _lastfm_callback(self, request: web.Request) -> web.Response:
        if not self._lastfm or not self._lastfm.available:
            return web.Response(text=safe_page("Last.fm indisponível", "A integração não está configurada."), content_type="text/html", status=503)
        state = request.query.get("state", "")
        token = request.query.get("token", "")
        if len(state) > 256 or len(token) > 512:
            return web.Response(text=safe_page("Falha na vinculação", "Os parâmetros recebidos são inválidos."), content_type="text/html", status=400)
        if not state or not token:
            return web.Response(text=safe_page("Falha na vinculação", "O Last.fm não forneceu os dados necessários."), content_type="text/html", status=400)
        user_id = self._lastfm.repository.consume_state(state)
        if user_id is None:
            return web.Response(text=safe_page("Falha na vinculação", "O link expirou ou já foi utilizado."), content_type="text/html", status=400)
        try:
            username, session_key = await self._lastfm.exchange_token(token)
            self._lastfm.repository.save_account(user_id, username, session_key)
        except LastFmError:
            return web.Response(text=safe_page("Falha na vinculação", "Não foi possível concluir a autorização. Tente gerar um novo link."), content_type="text/html", status=502)
        return web.Response(text=safe_page("Last.fm conectado", "Sua conta foi vinculada com sucesso. Você pode fechar esta janela."), content_type="text/html")

    async def _site_info(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "ayla-bot", "dailyRoute": "/api/daily-ayla"})

    async def _monsters(self, request: web.Request) -> web.Response:
        """Return the canonical UNO Caos monster catalog as JSON.

        The website must never receive the SPA fallback for this endpoint.  The
        catalog is derived from the same character table used by the game, so
        the panel and the bot cannot silently drift apart.
        """
        monsters = self._load_monsters()
        return web.json_response({"monsters": monsters})

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
            {
                "id": monster_id,
                "name": values["name"],
                "image": f"/api/uno/monsters/assets/{monster_id}.png",
                "exists": True,
                "appearanceChance": 100,
                "behavior": "mixed",
                "trigger": "on_appear",
                "actions": [{"type": "chaos_effect", "target": "random_player", "params": {"effects": values["effects"]}}],
                "metadata": {"effects": values["effects"]},
            }
            for monster_id, values in CHAOS_CHARACTERS.items()
        ]
        self._save_monsters(monsters)
        return monsters

    def _save_monsters(self, monsters: list[dict]) -> None:
        path = self._monster_catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(monsters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _admin_auth_failure(self, request: web.Request) -> web.Response | None:
        return _auth_failure_response(request, self._settings.site_admin_api_key)

    async def _create_monster(self, request: web.Request) -> web.Response:
        auth_failure = self._admin_auth_failure(request)
        if auth_failure:
            return auth_failure
        try:
            monster = _validate_monster(await request.json())
        except (json.JSONDecodeError, ValueError, TypeError, web.HTTPException) as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)
        monsters = self._load_monsters()
        if any(item.get("id") == monster["id"] for item in monsters):
            return web.json_response({"ok": False, "message": "Monstro ja existe."}, status=409)
        monsters.append(monster)
        self._save_monsters(monsters)
        return web.json_response(monster, status=201)

    async def _update_monster(self, request: web.Request) -> web.Response:
        auth_failure = self._admin_auth_failure(request)
        if auth_failure:
            return auth_failure
        monster_id = request.match_info["monster_id"]
        try:
            monster = _validate_monster(await request.json(), expected_id=monster_id)
        except (json.JSONDecodeError, ValueError, TypeError, web.HTTPException) as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)
        monsters = self._load_monsters()
        for index, current in enumerate(monsters):
            if current.get("id") == monster_id:
                monsters[index] = monster
                self._save_monsters(monsters)
                return web.json_response(monster)
        return web.json_response({"ok": False, "message": "Monstro nao encontrado."}, status=404)

    async def _delete_monster(self, request: web.Request) -> web.Response:
        auth_failure = self._admin_auth_failure(request)
        if auth_failure:
            return auth_failure
        monster_id = request.match_info["monster_id"]
        monsters = self._load_monsters()
        remaining = [item for item in monsters if item.get("id") != monster_id]
        if len(remaining) == len(monsters):
            return web.json_response({"ok": False, "message": "Monstro nao encontrado."}, status=404)
        self._save_monsters(remaining)
        return web.json_response({"ok": True, "id": monster_id})

    async def _upload_monster_image(self, request: web.Request) -> web.Response:
        auth_failure = self._admin_auth_failure(request)
        if auth_failure:
            return auth_failure
        monster_id = request.match_info["monster_id"]
        monsters = self._load_monsters()
        if not any(item.get("id") == monster_id for item in monsters):
            return web.json_response({"ok": False, "message": "Monstro nao encontrado."}, status=404)
        try:
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != "file":
                raise ValueError("Envie o arquivo no campo 'file'.")
            filename = Path(field.filename or "").name
            extension = Path(filename).suffix.lower()
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
        return web.json_response(
            {
                "actionTypes": ["chaos_effect", "draw", "skip", "set_color", "transform", "custom"],
                "targets": ["current_player", "random_player", "all_players", "player_with_most_cards"],
                "behaviors": ["mixed", "aggressive", "supportive", "chaotic"],
                "triggers": ["on_appear", "on_turn", "on_draw", "on_play"],
                "actionFields": {
                    "chaos_effect": ["target", "params"],
                    "draw": ["target", "amount"],
                    "skip": ["target"],
                    "set_color": ["target", "color"],
                    "transform": ["target", "amount"],
                    "custom": ["target", "params"],
                },
            }
        )

    async def _health(self, request: web.Request) -> web.Response:
        if not self._readiness_check():
            return web.json_response({"ok": False, "status": "starting"}, status=503)
        return web.json_response({"ok": True, "status": "ready"})

    async def _daily(self, request: web.Request) -> web.Response:
        auth_failure = _auth_failure_response(request, self._settings.site_api_key, missing_status=503)
        if auth_failure:
            return auth_failure
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "message": "JSON invalido."}, status=400)

        raw_user_id = payload.get("discordUserId") or payload.get("userId") or payload.get("discord_user_id")
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "message": "discordUserId invalido."}, status=400)

        bypass_cooldown = (
            payload.get("bypassCooldown") is True
            and payload.get("source") == "site"
            and bool(self._settings.site_api_key)
        )
        claim = self._economy.claim_daily(user_id, bypass_cooldown=bypass_cooldown)
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
                    # Preview do valor do daily; o bloqueio nao altera o saldo.
                    "amount": claim.amount or DAILY_AMOUNT,
                    "bonus": claim.bonus,
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

    def _internal_authorized(self, request: web.Request) -> bool:
        supplied = request.headers.get("authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:].strip()
        return valid_internal_token(self._settings.ayla_minecraft_internal_token, supplied)

    async def _minecraft_link_request(self, request: web.Request) -> web.Response:
        if not self._internal_authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            payload = await request.json()
            result = self._minecraft.request_link_code(
                payload.get("platform"), payload.get("external_id"), payload.get("username"),
                requested_ip=request.remote,
            )
            return web.json_response(result)
        except (MinecraftLinkError, AttributeError, TypeError, json.JSONDecodeError) as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)

    async def _minecraft_java_lookup(self, request: web.Request) -> web.Response:
        return await self._minecraft_account_lookup(request, forced_platform="java")

    async def _minecraft_account_lookup(self, request: web.Request, forced_platform: str | None = None) -> web.Response:
        if not self._internal_authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            platform = forced_platform or request.match_info["platform"]
            return web.json_response(self._minecraft.lookup_account(platform, request.match_info["external_id"]))
        except MinecraftLinkError as error:
            return web.json_response({"ok": False, "message": str(error)}, status=400)


def _cors_middleware(origin: str):
    allowed = _parse_allowed_origins(origin)

    @web.middleware
    async def middleware(request: web.Request, handler):
        response = await handler(request)
        requested = request.headers.get("Origin", "").rstrip("/").lower()
        if requested and requested in allowed:
            response.headers["Access-Control-Allow-Origin"] = requested
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Headers"] = "content-type, authorization, x-api-key"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    return middleware


def _parse_allowed_origins(value: str) -> set[str]:
    from urllib.parse import urlsplit

    origins = set()
    for raw in value.split(","):
        candidate = raw.strip().rstrip("/")
        parsed = urlsplit(candidate)
        if candidate == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            continue
        origins.add(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
    return origins


def _api_security_middleware(settings: Settings):
    @web.middleware
    async def middleware(request: web.Request, handler):
        import secrets
        request_id = secrets.token_hex(16)

        scope = api_scope_for(request.path)
        if scope is not None and request.method != "OPTIONS":
            expected = {
                "site-user": settings.site_api_key,
                "site-admin": settings.site_admin_api_key,
                "minecraft": settings.ayla_minecraft_internal_token,
            }.get(scope)
            if scope != "public":
                if scope == "minecraft":
                    supplied = request.headers.get("authorization", "")
                    if supplied.lower().startswith("bearer "):
                        supplied = supplied[7:].strip()
                    authorized = valid_internal_token(expected, supplied)
                    response = None if authorized else web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers={"WWW-Authenticate": "Bearer"})
                else:
                    response = _auth_failure_response(request, expected, missing_status=503 if scope == "site-user" else 401)
                if response:
                    response.headers["X-Request-Id"] = request_id
                    return response

        body_limit = 10 * 1024 * 1024 + 512 * 1024 if request.path.endswith("/image") else 1024 * 1024
        if request.content_length is not None and request.content_length > body_limit:
            response = web.json_response({"ok": False, "error": "request_too_large", "request_id": request_id}, status=413)
            response.headers["X-Request-Id"] = request_id
            return response
        if request.content_length is None and request.can_read_body and not request.path.endswith("/image"):
            try:
                async with asyncio.timeout(30):
                    body = await request.read()
            except TimeoutError:
                response = web.json_response({"ok": False, "error": "request_timeout", "request_id": request_id}, status=504)
                response.headers["X-Request-Id"] = request_id
                return response
            except web.HTTPRequestEntityTooLarge:
                response = web.json_response({"ok": False, "error": "request_too_large", "request_id": request_id}, status=413)
                response.headers["X-Request-Id"] = request_id
                return response
            if len(body) > body_limit:
                response = web.json_response({"ok": False, "error": "request_too_large", "request_id": request_id}, status=413)
                response.headers["X-Request-Id"] = request_id
                return response

        try:
            async with asyncio.timeout(30):
                response = await handler(request)
        except TimeoutError:
            response = web.json_response({"ok": False, "error": "request_timeout", "request_id": request_id}, status=504)
        except web.HTTPException as error:
            response = error
        except Exception as error:
            logger.error("site_api_request_failed request_id=%s error_type=%s", request_id, type(error).__name__)
            response = web.json_response({"ok": False, "error": "internal_error", "request_id": request_id}, status=500)
        response.headers["X-Request-Id"] = request_id
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
    return {
        "id": monster_id,
        "name": name,
        "image": str(payload.get("image") or f"/api/uno/monsters/assets/{monster_id}.png"),
        "exists": payload.get("exists") is not False,
        "appearanceChance": int(chance),
        "behavior": str(payload.get("behavior") or "mixed"),
        "trigger": str(payload.get("trigger") or "on_appear"),
        "actions": actions,
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    }
