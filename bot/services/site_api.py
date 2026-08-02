from aiohttp import web

from bot.config import Settings
from bot.services.economy_service import DAILY_AMOUNT
from bot.services.economy_service import EconomyService


class SiteApiServer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._economy = EconomyService(settings)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if self._runner:
            return

        app = web.Application(middlewares=[_cors_middleware(self._settings.site_api_cors_origin)])
        app.router.add_get("/api/site", self._site_info)
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

        profile, remaining = self._economy.claim_daily(user_id)
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
                "amount": DAILY_AMOUNT,
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
