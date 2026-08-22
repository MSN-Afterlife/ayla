import json
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)
CLICKUP_PRIORITY_VALUES = {"urgent": 1, "high": 2, "normal": 3, "low": 4}


class ClickUpError(Exception):
    """Erro seguro para expor ao handler sem vazar detalhes da API."""


class ClickUpService:
    _task_endpoint = "https://api.clickup.com/api/v2/list/{list_id}/task"
    _field_endpoint = "https://api.clickup.com/api/v2/list/{list_id}/field"
    _get_task_endpoint = "https://api.clickup.com/api/v2/task/{task_id}"

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
        priority: str | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        *,
        context: str = "task",
    ) -> dict[str, Any]:
        self._validate_common(list_id, name)
        endpoint = self._task_endpoint.format(list_id=list_id)
        numeric_priority = priority_to_clickup(priority, context=context)
        request_payload: dict[str, Any] = {"name": name[:200], "markdown_content": description}
        if numeric_priority is not None:
            request_payload["priority"] = numeric_priority
        if custom_fields:
            request_payload["custom_fields"] = custom_fields
        if parent_task_id:
            request_payload["parent"] = parent_task_id
        logger.info(
            "ClickUp request context=%s list_id=%s parent_id=%s priority=%s/%s custom_fields=%s payload=%s",
            context, list_id, parent_task_id or "-", priority, numeric_priority,
            [field.get("id") for field in custom_fields or []], request_payload,
        )
        response_payload, status = await self._request("POST", endpoint, request_payload, context=context)
        task_id = response_payload.get("id") if isinstance(response_payload, dict) else None
        logger.info("ClickUp response context=%s status=%s task_id=%s body=%s", context, status, task_id or "-", response_payload)
        return response_payload

    async def create_subtask(self, list_id: str, parent_task_id: str, name: str, description: str, priority: str | None = None, custom_fields: list[dict[str, Any]] | None = None, *, context: str = "subtask") -> dict[str, Any]:
        return await self.create_task(list_id, name, description, parent_task_id=parent_task_id, priority=priority, custom_fields=custom_fields, context=context)

    async def get_list_custom_fields(self, list_id: str) -> list[dict[str, Any]]:
        if not list_id.strip():
            raise ClickUpError("list_id vazio ao buscar Custom Fields.")
        payload, status = await self._request("GET", self._field_endpoint.format(list_id=list_id), context="custom_fields")
        fields = payload.get("fields") if isinstance(payload, dict) else None
        if not isinstance(fields, list):
            raise ClickUpError("Resposta de Custom Fields sem a lista fields.")
        logger.info("ClickUp custom fields list_id=%s status=%s count=%s", list_id, status, len(fields))
        return [field for field in fields if isinstance(field, dict)]

    async def get_task(self, task_id: str) -> dict[str, Any]:
        payload, status = await self._request("GET", self._get_task_endpoint.format(task_id=task_id), context="verify_task")
        logger.info("ClickUp verification task_id=%s status=%s body=%s", task_id, status, payload)
        return payload

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _validate_common(self, list_id: str, name: str) -> None:
        if not self._api_token:
            raise ClickUpError("CLICKUP_API_TOKEN não está configurado.")
        if not isinstance(list_id, str) or not list_id.strip():
            raise ClickUpError("list_id do ClickUp não está configurado.")
        if not name.strip():
            raise ClickUpError("O título da tarefa não pode ficar vazio.")

    async def _request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None, *, context: str) -> tuple[dict[str, Any], int]:
        if not self._api_token:
            raise ClickUpError("CLICKUP_API_TOKEN não está configurado.")
        try:
            session = await self._get_session()
            async with session.request(method, endpoint, headers={"Authorization": self._api_token, "Content-Type": "application/json"}, json=payload) as response:
                response_text = await response.text()
                if response.status < 200 or response.status >= 300:
                    logger.error("ClickUp API failed context=%s status=%s endpoint=%s body=%s", context, response.status, endpoint, _safe_response_body(response_text))
                    raise ClickUpError(f"ClickUp respondeu HTTP {response.status} em {context}.")
                try:
                    parsed = json.loads(response_text) if response_text else {}
                except json.JSONDecodeError:
                    logger.error("ClickUp API invalid JSON context=%s status=%s body=%s", context, response.status, _safe_response_body(response_text))
                    raise ClickUpError("A resposta do ClickUp era inválida.") from None
                if not isinstance(parsed, dict):
                    raise ClickUpError("A resposta do ClickUp era inválida.")
                return parsed, response.status
        except ClickUpError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            logger.error("ClickUp API connection failed context=%s endpoint=%s error=%s", context, endpoint, type(error).__name__)
            raise ClickUpError("Não foi possível conectar ao ClickUp.") from error

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session


def priority_to_clickup(priority: Any, *, context: str = "task") -> int | None:
    if priority is None:
        logger.warning("ClickUp priority missing context=%s; field omitted reason=no_semantic_value", context)
        return None
    if not isinstance(priority, str) or priority not in CLICKUP_PRIORITY_VALUES:
        logger.warning("ClickUp priority invalid context=%s value=%r; field omitted reason=not_allowed", context, priority)
        return None
    return CLICKUP_PRIORITY_VALUES[priority]


def _safe_response_body(body: str, limit: int = 1000) -> str:
    return body.replace("\n", " ")[:limit] or "<vazio>"
