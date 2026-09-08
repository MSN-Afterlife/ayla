from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from typing import Any

from lib.generic.domain import MigrationIdentity, MigrationInspection, MigrationPlan


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 10.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class MigrationDaemonClient:
    def __init__(
        self,
        token: str,
        socket_path: Path | str | None = None,
        base_url: str = "127.0.0.1:8099",
        timeout: float = 10.0,
    ) -> None:
        self.token = str(token).strip()
        self.socket_path = str(socket_path) if socket_path else None
        self.base_url = base_url
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.socket_path and Path(self.socket_path).exists():
            conn = UnixHTTPConnection(self.socket_path, timeout=self.timeout)
        else:
            host, port = self.base_url.split(":")
            conn = http.client.HTTPConnection(host, int(port), timeout=self.timeout)

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "MigrationDaemonClient/1.0",
        }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None

        try:
            conn.request(method, path, body=body, headers=headers)
            res = conn.getresponse()
            raw_data = res.read().decode("utf-8")
            status = res.status
        except Exception as err:
            raise ConnectionError(f"Falha ao comunicar com o Migration Daemon: {err}") from err
        finally:
            conn.close()

        try:
            data = json.loads(raw_data)
        except Exception:
            data = {"error": raw_data}

        if status >= 400:
            msg = data.get("error", f"HTTP {status}")
            if status == 401:
                raise PermissionError(f"Acesso negado pelo daemon: {msg}")
            if status == 422:
                raise ValueError(f"Dados rejeitados pelo daemon: {msg}")
            raise RuntimeError(f"Erro do daemon (HTTP {status}): {msg}")

        return data

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def inspect(self, identity: MigrationIdentity) -> MigrationInspection:
        data = self._request("POST", "/api/v1/players/inspect", {"identity": identity.to_dict()})
        return MigrationInspection.from_dict(data)

    def plan(self, identity: MigrationIdentity) -> MigrationPlan:
        data = self._request("POST", "/api/v1/migrations/plan", {"identity": identity.to_dict()})
        return MigrationPlan.from_dict(data)
