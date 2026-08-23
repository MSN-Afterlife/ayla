from aiohttp import web

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
from bot.services.economy_service import EconomyService
from bot.services.lastfm_service import LastFmError, LastFmService, safe_page


class SiteApiServer:
    def __init__(self, settings: Settings, lastfm: LastFmService | None = None) -> None:
        self._settings = settings
        self._economy = EconomyService(settings)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._lastfm = lastfm

    async def start(self) -> None:
        if self._runner:
            return

        app = web.Application(middlewares=[_cors_middleware(self._settings.site_api_cors_origin)])
        app.router.add_get("/api/site", self._site_info)
        app.router.add_post("/api/daily-ayla", self._daily)
        app.router.add_options("/api/daily-ayla", self._options)
        app.router.add_get("/auth/lastfm/callback", self._lastfm_callback)

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
