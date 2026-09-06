from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import socketserver
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Add lib to sys.path if needed
ENGINE_ROOT = Path("/opt/minecraft/migration-engine").resolve()
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from lib.generic.domain import MigrationIdentity
from lib.generic.inspector import GenericInspector
from lib.generic.phase2b import FeatureFlags, IdempotencyConflictError, Phase2BMigrationService, WriteDisabledError
from lib.generic.planner import GenericPlanner

DEFAULT_SERVER_ROOT = Path("/opt/minecraft/crafty/servers/c253fa7e-2bd2-4545-a9ba-b1a8db892197")
DEFAULT_TOKEN_PATH = ENGINE_ROOT / "config/daemon.token"
DEFAULT_SOCKET_PATH = Path("/opt/ayla/data/staging/migration-engine.sock")
DEFAULT_STATE_DIR = ENGINE_ROOT / "state" / "phase2b"

logger = logging.getLogger("migration_daemon")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [req:%(request_id)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)


def load_token(token_path: Path | str = DEFAULT_TOKEN_PATH) -> str:
    path = Path(token_path)
    if not path.is_file():
        raise RuntimeError(f"Arquivo de token inexistente: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token or len(token) < 16:
        raise RuntimeError("Token de autenticação inválido ou muito curto.")
    return token


class DaemonRequestHandler(BaseHTTPRequestHandler):
    server_root: Path = DEFAULT_SERVER_ROOT
    expected_token: str = ""

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default noisy stdlib logging; handled in custom logger
        pass

    def _send_json(self, status: int, data: dict[str, Any], req_id: str) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", req_id)
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        supplied = auth_header[7:].strip()
        return bool(supplied and hmac.compare_digest(self.expected_token, supplied))

    def do_GET(self) -> None:
        start_time = time.time()
        req_id = f"req-{secrets.token_hex(4)}"
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._send_json(200, {
                "status": "ok",
                "mode": "READ_ONLY",
                "server_root": str(self.server_root),
                "write_api": "PREPARED_DISABLED",
                "write_enabled": FeatureFlags.from_env().write_enabled,
                "production_enabled": FeatureFlags.from_env().production_enabled,
                "timestamp": time.time(),
            }, req_id)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info("GET /health 200 %dms", elapsed_ms, extra={"request_id": req_id})
            return

        if parsed.path == "/api/v1/gateway/lock":
            if not self._check_auth():
                self._send_json(401, {"error": "Não autorizado: Token Bearer ausente ou inválido."}, req_id)
                logger.warning("GET %s 401 (Auth failure)", self.path, extra={"request_id": req_id})
                return
            key = (parse_qs(parsed.query).get("key") or [""])[0].strip().lower()
            if not key:
                self._send_json(400, {"error": "key é obrigatório."}, req_id)
                return
            service = Phase2BMigrationService(self.server_root, DEFAULT_STATE_DIR)
            allowed, message = service.gateway.evaluate_login(key)
            self._send_json(200, {"locked": not allowed, "deny_message": message}, req_id)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info("GET /api/v1/gateway/lock 200 %dms locked=%s", elapsed_ms, not allowed, extra={"request_id": req_id})
            return

        if parsed.path.startswith("/api/v1/migrations/") and not parsed.path.endswith("/rollback"):
            if not self._check_auth():
                self._send_json(401, {"error": "Não autorizado: Token Bearer ausente ou inválido."}, req_id)
                logger.warning("GET %s 401 (Auth failure)", self.path, extra={"request_id": req_id})
                return
            migration_id = parsed.path.removeprefix("/api/v1/migrations/").strip("/")
            if not migration_id or "/" in migration_id:
                self._send_json(404, {"error": "Endpoint não encontrado"}, req_id)
                return
            service = Phase2BMigrationService(self.server_root, DEFAULT_STATE_DIR)
            self._send_json(200, service.status(migration_id), req_id)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info("GET /api/v1/migrations/%s 200 %dms", migration_id, elapsed_ms, extra={"request_id": req_id})
            return

        self._send_json(404, {"error": "Endpoint não encontrado"}, req_id)
        logger.warning("GET %s 404", self.path, extra={"request_id": req_id})

    def do_POST(self) -> None:
        start_time = time.time()
        req_id = f"req-{secrets.token_hex(4)}"

        write_execute = self.path == "/api/v1/migrations/execute"
        write_rollback = self.path.startswith("/api/v1/migrations/") and self.path.endswith("/rollback")

        # 1. Path routing check
        if self.path not in ("/api/v1/players/inspect", "/api/v1/migrations/plan") and not write_execute and not write_rollback:
            self._send_json(404, {"error": f"Rota não suportada: {self.path}"}, req_id)
            logger.warning("POST %s 404", self.path, extra={"request_id": req_id})
            return

        # 2. Authentication check
        if not self._check_auth():
            self._send_json(401, {"error": "Não autorizado: Token Bearer ausente ou inválido."}, req_id)
            logger.warning("POST %s 401 (Auth failure)", self.path, extra={"request_id": req_id})
            return

        # 3. Read Body with length limit (64KB)
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0 or content_length > 65536:
                self._send_json(400, {"error": "Corpo da requisição ausente ou excede 64KB."}, req_id)
                return
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as err:
            self._send_json(400, {"error": f"JSON inválido ou malformado: {err}"}, req_id)
            return

        # 4. Strict field validation (prevent unknown / injection fields)
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Payload deve ser um objeto JSON."}, req_id)
            return

        if write_execute or write_rollback:
            self._handle_write_endpoint(payload, req_id, write_execute=write_execute)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info("POST %s %dms", self.path, elapsed_ms, extra={"request_id": req_id})
            return

        allowed_keys = {"identity", "options"}
        if any(k not in allowed_keys for k in payload.keys()):
            self._send_json(400, {"error": "Campos desconhecidos não permitidos no payload."}, req_id)
            return

        identity_data = payload.get("identity")
        if not identity_data or not isinstance(identity_data, dict):
            self._send_json(422, {"error": "Campo 'identity' é obrigatório e deve ser um objeto."}, req_id)
            return

        # 5. Parse Identity Model
        try:
            identity = MigrationIdentity.from_dict(identity_data)
        except Exception as err:
            self._send_json(422, {"error": f"Identidade inválida: {err}"}, req_id)
            return

        # 6. Execute Read-Only Operation
        try:
            inspector = GenericInspector(self.server_root)
            inspection = inspector.inspect(identity)

            if self.path == "/api/v1/players/inspect":
                self._send_json(200, inspection.to_dict(), req_id)
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "POST /api/v1/players/inspect 200 %dms identity=%s blockers=%d",
                    elapsed_ms, identity.canonical_name, len(inspection.blockers),
                    extra={"request_id": req_id},
                )
                return

            if self.path == "/api/v1/migrations/plan":
                planner = GenericPlanner()
                plan = planner.create_plan(inspection)
                self._send_json(200, plan.to_dict(), req_id)
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "POST /api/v1/migrations/plan 200 %dms identity=%s status=%s",
                    elapsed_ms, identity.canonical_name, plan.status,
                    extra={"request_id": req_id},
                )
                return

        except Exception as err:
            self._send_json(500, {"error": f"Erro interno durante inspeção: {err}"}, req_id)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error("POST %s 500 %dms err=%s", self.path, elapsed_ms, err, extra={"request_id": req_id})

    def _handle_write_endpoint(self, payload: dict[str, Any], req_id: str, *, write_execute: bool) -> None:
        idempotency_key = self.headers.get("Idempotency-Key")
        if not idempotency_key:
            self._send_json(400, {"error": "Idempotency-Key é obrigatório."}, req_id)
            return
        flags = FeatureFlags.from_env()
        if not flags.write_enabled or not flags.production_enabled:
            self._send_json(403, {"error": "MIGRATION_WRITE_ENABLED and MIGRATION_PRODUCTION_ENABLED must both be true.", "code": "MIGRATION_WRITE_DISABLED"}, req_id)
            return
        service = Phase2BMigrationService(self.server_root, DEFAULT_STATE_DIR)
        try:
            if write_execute:
                result = service.execute({**payload, "request_id": req_id}, idempotency_key=idempotency_key)
            else:
                parts = self.path.strip("/").split("/")
                migration_id = parts[3] if len(parts) >= 5 else ""
                result = service.rollback(migration_id, {**payload, "request_id": req_id}, idempotency_key=idempotency_key)
            self._send_json(200, result, req_id)
        except WriteDisabledError as err:
            self._send_json(403, {"error": str(err), "code": "MIGRATION_WRITE_DISABLED"}, req_id)
        except IdempotencyConflictError as err:
            self._send_json(409, {"error": str(err), "code": "IDEMPOTENCY_CONFLICT"}, req_id)
        except Exception as err:
            self._send_json(422, {"error": str(err)}, req_id)


class UnixThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class TCPThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_handler(server_root: Path, token: str) -> type[DaemonRequestHandler]:
    class BoundHandler(DaemonRequestHandler):
        pass
    BoundHandler.server_root = Path(server_root).resolve()
    BoundHandler.expected_token = token
    return BoundHandler


def run_daemon(
    socket_path: Path | str | None = DEFAULT_SOCKET_PATH,
    tcp_port: int | None = 8099,
    server_root: Path | str = DEFAULT_SERVER_ROOT,
    token_path: Path | str = DEFAULT_TOKEN_PATH,
) -> None:
    token = load_token(token_path)
    handler_cls = make_handler(Path(server_root), token)

    servers = []

    if socket_path:
        sock_p = Path(socket_path)
        sock_p.parent.mkdir(parents=True, exist_ok=True)
        if sock_p.exists():
            sock_p.unlink()
        unix_server = UnixThreadingHTTPServer(str(sock_p), handler_cls)
        # Grant read/write to current user and group
        os.chmod(str(sock_p), 0o666)
        servers.append(unix_server)
        logger.info("Daemon escutando em Unix Domain Socket: %s", sock_p, extra={"request_id": "init"})

    if tcp_port:
        tcp_server = TCPThreadingHTTPServer(("127.0.0.1", tcp_port), handler_cls)
        servers.append(tcp_server)
        logger.info("Daemon escutando em TCP local: 127.0.0.1:%d", tcp_port, extra={"request_id": "init"})

    if not servers:
        raise RuntimeError("Nenhum transporte configurado (socket ou tcp).")

    import threading
    threads = []
    for s in servers[1:]:
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        threads.append(t)

    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando daemon...", extra={"request_id": "exit"})
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
        if socket_path and Path(socket_path).exists():
            try:
                Path(socket_path).unlink()
            except OSError:
                pass


if __name__ == "__main__":
    run_daemon()
