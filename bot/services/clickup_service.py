import logging
from typing import Any

import aiohttp


logger = logging.getLogger(__name__)


class ClickUpError(Exception):
    """Erro seguro para expor ao handler sem vazar detalhes da API."""


class ClickUpService:
    _endpoint = "https://api.clickup.com/api/v2/list/{list_id}/task"

    def __init__(self, api_token: str | None, *, timeout_seconds: float = 15) -> None:
        self._api_token = api_token
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def create_task(
        self,
        list_id: str,
        name: str,
        description: str,
        parent_task_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._api_token:
            raise ClickUpError("CLICKUP_API_TOKEN não está configurado.")
        if not list_id.strip():
            raise ClickUpError("CLICKUP_LIST_ID não está configurado.")
        if not name.strip():
            raise ClickUpError("O título da tarefa não pode ficar vazio.")

        endpoint = self._endpoint.format(list_id=list_id)
        try:
            session = await self._get_session()
            payload = {"name": name[:200], "markdown_content": description}
            if parent_task_id:
                payload["parent"] = parent_task_id
            async with session.post(
                endpoint,
                headers={"Authorization": self._api_token, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                response_text = await response.text()
                if response.status < 200 or response.status >= 300:
                    logger.error(
                        "ClickUp task creation failed: HTTP %s endpoint=%s body=%s",
                        response.status,
                        endpoint,
                        _safe_response_body(response_text),
                    )
                    raise ClickUpError(f"ClickUp respondeu HTTP {response.status}.")

                try:
                    payload = await _json_from_text(response_text)
                except ValueError:
                    logger.error("ClickUp task creation failed: resposta JSON inválida endpoint=%s", endpoint)
                    raise ClickUpError("A resposta do ClickUp era inválida.") from None
                if not isinstance(payload, dict):
                    raise ClickUpError("A resposta do ClickUp era inválida.")
                return payload
        except ClickUpError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            logger.error("ClickUp task creation failed: endpoint=%s error=%s", endpoint, error)
            raise ClickUpError("Não foi possível conectar ao ClickUp.") from error

    async def create_subtask(
        self,
        list_id: str,
        parent_task_id: str,
        name: str,
        description: str,
    ) -> dict[str, Any]:
        return await self.create_task(list_id, name, description, parent_task_id=parent_task_id)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session


async def _json_from_text(text: str) -> Any:
    import json

    return json.loads(text)


def _safe_response_body(body: str, limit: int = 1000) -> str:
    return body.replace("\n", " ")[:limit] or "<vazio>"
