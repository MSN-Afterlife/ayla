import json
import logging
import time
from urllib.parse import quote
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

    def __init__(self, api_token: str | None, *, timeout_seconds: float = 15, free_mode: bool = False) -> None:
        self._api_token = api_token
        self._free_mode = free_mode
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._catalog_cache: list[dict[str, str]] | None = None
        self._catalog_cached_at = 0.0

    async def create_task(
        self,
        list_id: str,
        name: str,
        description: str,
        parent_task_id: str | None = None,
        priority: str | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        time_estimate: int | None = None,
        points: int | None = None,
        tags: list[str] | None = None,
        custom_item_id: int | None = None,
        *,
        context: str = "task",
    ) -> dict[str, Any]:
        self._validate_common(list_id, name)
        if self._free_mode:
            # Explicit 0 forces a standard Task even when the List default is Bug.
            if custom_fields:
                logger.info("ClickUp Free mode: omitting %s Custom Fields", len(custom_fields))
            custom_fields = None
            custom_item_id = 0
        endpoint = self._task_endpoint.format(list_id=list_id)
        numeric_priority = priority_to_clickup(priority, context=context)
        request_payload: dict[str, Any] = {"name": name[:200], "markdown_content": description}
        if numeric_priority is not None:
            request_payload["priority"] = numeric_priority
        if custom_fields:
            request_payload["custom_fields"] = custom_fields
        if time_estimate is not None:
            request_payload["time_estimate"] = time_estimate
        if points is not None:
            request_payload["points"] = points
        if tags:
            request_payload["tags"] = tags
        if parent_task_id:
            request_payload["parent"] = parent_task_id
        if custom_item_id is not None:
            request_payload["custom_item_id"] = custom_item_id
        logger.info(
            "ClickUp request context=%s list_id=%s parent_id=%s priority=%s/%s custom_fields=%s payload=%s",
            context, list_id, parent_task_id or "-", priority, numeric_priority,
            [field.get("id") for field in custom_fields or []], request_payload,
        )
        try:
            response_payload, status = await self._request("POST", endpoint, request_payload, context=context)
        except ClickUpError as error:
            if custom_item_id is None and _is_custom_task_type_limit(error):
                logger.warning("ClickUp custom task type limit context=%s; retrying standard Task", context)
                retry_payload = dict(request_payload)
                retry_payload["custom_item_id"] = 0
                response_payload, status = await self._request("POST", endpoint, retry_payload, context=f"{context}_standard_task")
            elif points is not None and _is_sprint_points_disabled(error):
                logger.warning("ClickUp Sprint Points unavailable context=%s; retrying task without points", context)
                retry_payload = dict(request_payload)
                retry_payload.pop("points", None)
                response_payload, status = await self._request("POST", endpoint, retry_payload, context=f"{context}_without_points")
            else:
                raise
        task_id = response_payload.get("id") if isinstance(response_payload, dict) else None
        logger.info("ClickUp response context=%s status=%s task_id=%s body=%s", context, status, task_id or "-", response_payload)
        return response_payload

    async def create_subtask(self, list_id: str, parent_task_id: str, name: str, description: str, priority: str | None = None, custom_fields: list[dict[str, Any]] | None = None, *, context: str = "subtask") -> dict[str, Any]:
        return await self.create_task(list_id, name, description, parent_task_id=parent_task_id, priority=priority, custom_fields=custom_fields, context=context, custom_item_id=0)

    async def get_space_tags(self, space_id: str) -> list[dict[str, Any]]:
        payload, _ = await self._request("GET", f"https://api.clickup.com/api/v2/space/{space_id}/tag", context="space_tags")
        tags = payload.get("tags") if isinstance(payload, dict) else None
        return [tag for tag in tags if isinstance(tag, dict)] if isinstance(tags, list) else []

    async def create_space_tag(self, space_id: str, tag_name: str) -> None:
        await self._request(
            "POST", f"https://api.clickup.com/api/v2/space/{space_id}/tag",
            {"tag": {"name": tag_name, "tag_fg": "#FFFFFF", "tag_bg": "#7B68EE"}},
            context="create_space_tag",
        )

    async def add_tag_to_task(self, task_id: str, tag_name: str) -> None:
        await self._request(
            "POST", f"https://api.clickup.com/api/v2/task/{task_id}/tag/{quote(tag_name, safe='')}",
            context="add_task_tag",
        )

    async def ensure_task_tags(self, task_id: str, space_id: str | None, tags: list[str]) -> None:
        if not tags:
            return
        existing: set[str] = set()
        if space_id:
            try:
                existing = {str(tag.get("name", "")).casefold() for tag in await self.get_space_tags(space_id)}
            except ClickUpError as error:
                logger.warning("ClickUp tags lookup failed space_id=%s error=%s", space_id, error)
        for tag_name in tags:
            if space_id and tag_name.casefold() not in existing:
                try:
                    await self.create_space_tag(space_id, tag_name)
                    existing.add(tag_name.casefold())
                except ClickUpError as error:
                    logger.warning("ClickUp tag creation failed tag=%s error=%s", tag_name, error)
            try:
                await self.add_tag_to_task(task_id, tag_name)
            except ClickUpError as error:
                logger.warning("ClickUp tag assignment failed task_id=%s tag=%s error=%s", task_id, tag_name, error)

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

    async def get_list_catalog(self, workspace_id: str | None = None, *, cache_seconds: int = 600) -> list[dict[str, str]]:
        if self._catalog_cache is not None and time.monotonic() - self._catalog_cached_at < cache_seconds:
            return list(self._catalog_cache)
        teams_payload, _ = await self._request("GET", "https://api.clickup.com/api/v2/team", context="catalog_workspaces")
        teams = teams_payload.get("teams") if isinstance(teams_payload, dict) else None
        if not isinstance(teams, list) or not teams:
            raise ClickUpError("ClickUp não retornou nenhum Workspace autorizado.")
        workspace = next((item for item in teams if str(item.get("id")) == str(workspace_id)), None) if workspace_id else teams[0]
        if not isinstance(workspace, dict) or not workspace.get("id"):
            raise ClickUpError(f"Workspace do ClickUp não encontrado: {workspace_id}.")
        workspace_name = str(workspace.get("name", workspace.get("id")))
        spaces_payload, _ = await self._request("GET", f"https://api.clickup.com/api/v2/team/{workspace['id']}/space", context="catalog_spaces")
        spaces = spaces_payload.get("spaces") if isinstance(spaces_payload, dict) else []
        catalog: list[dict[str, str]] = []
        for space in spaces if isinstance(spaces, list) else []:
            if not isinstance(space, dict) or not space.get("id"):
                continue
            space_name = str(space.get("name", space["id"]))
            folders_payload, _ = await self._request("GET", f"https://api.clickup.com/api/v2/space/{space['id']}/folder", context="catalog_folders")
            folders = folders_payload.get("folders") if isinstance(folders_payload, dict) else []
            for folder in folders if isinstance(folders, list) else []:
                if not isinstance(folder, dict) or not folder.get("id"):
                    continue
                folder_name = str(folder.get("name", folder["id"]))
                lists_payload, _ = await self._request("GET", f"https://api.clickup.com/api/v2/folder/{folder['id']}/list", context="catalog_lists")
                catalog.extend(_catalog_lists(lists_payload, workspace_name, space_name, folder_name, str(space["id"])))
            folderless_payload, _ = await self._request("GET", f"https://api.clickup.com/api/v2/space/{space['id']}/list", context="catalog_folderless_lists")
            catalog.extend(_catalog_lists(folderless_payload, workspace_name, space_name, None, str(space["id"])))
        if not catalog:
            raise ClickUpError(f"Nenhuma lista acessível encontrada no Workspace {workspace_name!r}.")
        self._catalog_cache = catalog
        self._catalog_cached_at = time.monotonic()
        logger.info("ClickUp catalog refreshed workspace=%s workspace_id=%s lists=%s", workspace_name, workspace["id"], len(catalog))
        return list(catalog)

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
                    detail = ""
                    try:
                        error_payload = json.loads(response_text)
                        if isinstance(error_payload, dict):
                            detail = f" {error_payload.get('ECODE', '')} {error_payload.get('err', '')}".strip()
                    except json.JSONDecodeError:
                        pass
                    raise ClickUpError(f"ClickUp respondeu HTTP {response.status} em {context}. {detail}".strip())
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


def _is_sprint_points_disabled(error: ClickUpError) -> bool:
    text = str(error).casefold()
    return "item_227" in text or "sprint points clickapp is not enabled" in text


def _is_custom_task_type_limit(error: ClickUpError) -> bool:
    text = str(error).casefold()
    return "item_246" in text or "max usage for custom task types reached" in text


def _safe_response_body(body: str, limit: int = 1000) -> str:
    return body.replace("\n", " ")[:limit] or "<vazio>"


def _catalog_lists(payload: Any, workspace: str, space: str, folder: str | None, space_id: str | None = None) -> list[dict[str, str]]:
    lists = payload.get("lists") if isinstance(payload, dict) else []
    result = []
    for item in lists if isinstance(lists, list) else []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            continue
        path = " > ".join(part for part in (workspace, space, folder, str(item["name"])) if part)
        result.append({"id": str(item["id"]), "name": str(item["name"]), "path": path, "space_id": str(space_id or "")})
    return result
