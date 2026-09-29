import asyncio
import json
import logging
import time
from typing import Any

import aiohttp

from bot.services.task_provider import TaskDraft

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_PRIORITY_VALUES = {"urgent": 1, "high": 2, "normal": 3, "low": 4}


class LinearError(Exception):
    """Safe provider error; never contains credentials or raw API responses."""


class LinearService:
    def __init__(self, api_key: str | None, *, timeout_seconds: float = 15, cache_seconds: int = 600, endpoint: str = LINEAR_API_URL) -> None:
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._cache_seconds = cache_seconds
        self._endpoint = endpoint
        self._session: aiohttp.ClientSession | None = None
        self._metadata: dict[str, Any] | None = None
        self._metadata_cached_at = 0.0

    async def create_task(self, draft: TaskDraft, *, context: str = "task") -> dict[str, Any]:
        self._validate(draft)
        metadata = await self.get_metadata()
        team_name, project_name = self.route(draft)
        team = self._find_name(metadata["teams"], team_name)
        if not team:
            raise LinearError(f"Team Linear não encontrado: {team_name}.")
        project = self._find_project(metadata["projects"], project_name, team["id"])
        if project_name and not project:
            raise LinearError(f"Projeto Linear não encontrado: {project_name}.")
        label_ids = self.resolve_labels(metadata, draft, team_id=str(team["id"]))
        state = self._find_state(metadata["states"], "Todo", team["id"])
        variables: dict[str, Any] = {
            "input": {
                "teamId": team["id"],
                "title": draft.title[:200],
                "description": draft.description[:12000],
                "priority": LINEAR_PRIORITY_VALUES.get(draft.priority, 3),
                "labelIds": label_ids,
            }
        }
        if project:
            variables["input"]["projectId"] = project["id"]
        if state:
            variables["input"]["stateId"] = state["id"]
        payload = await self._graphql(
            """mutation IssueCreate($input: IssueCreateInput!) {
                issueCreate(input: $input) { success issue { id identifier url title } }
            }""",
            variables,
            context=context,
        )
        result = payload.get("issueCreate") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or result.get("success") is not True or not isinstance(result.get("issue"), dict):
            raise LinearError("Linear não confirmou a criação da issue.")
        issue = result["issue"]
        logger.info("Linear issue created context=%s issue_id=%s identifier=%s", context, issue.get("id", "-"), issue.get("identifier", "-"))
        return issue

    async def get_metadata(self) -> dict[str, Any]:
        if self._metadata is not None and time.monotonic() - self._metadata_cached_at < self._cache_seconds:
            return self._metadata
        teams = await self._graphql("query { teams(first: 250) { nodes { id name key } } }", {}, context="teams")
        projects = await self._graphql("query { projects(first: 250) { nodes { id name teams { nodes { id } } } } }", {}, context="projects")
        labels = await self._graphql("query { issueLabels(first: 250) { nodes { id name isGroup parent { name } team { id } } } }", {}, context="labels")
        states = await self._graphql("query { workflowStates(first: 250) { nodes { id name type team { id } } } }", {}, context="states")
        metadata = {
            "teams": self._nodes(teams, "teams"),
            "projects": self._nodes(projects, "projects"),
            "labels": self._nodes(labels, "issueLabels"),
            "states": self._nodes(states, "workflowStates"),
        }
        if not metadata["teams"]:
            raise LinearError("Linear não retornou teams.")
        self._metadata = metadata
        self._metadata_cached_at = time.monotonic()
        logger.info("Linear metadata refreshed teams=%s projects=%s labels=%s states=%s", *(len(metadata[key]) for key in ("teams", "projects", "labels", "states")))
        return metadata

    @staticmethod
    def route(draft: TaskDraft) -> tuple[str, str | None]:
        text = f"{draft.title} {draft.description}".casefold()
        if draft.project:
            project = draft.project
        elif draft.area in {"minecraft"} or any(word in text for word in ("minecraft", "velocity", "/skin")):
            project = "Minecraft Server"
        elif draft.area == "discord" or "discord" in text:
            project = "Discord Server"
        elif draft.area in {"infrastructure", "network", "devops"} or any(word in text for word in ("vps", "rede", "infraestrutura", "servidor")):
            project = "Infraestrutura"
        elif draft.area == "site" or any(word in text for word in ("site", "frontend", "front-end")):
            project = "Site"
        elif draft.area in {"ayla", "api", "backend", "database", "security"}:
            project = "Ayla"
        else:
            project = None
        team = draft.team or ("Operations" if project in {"Minecraft Server", "Discord Server", "Infraestrutura"} else "Software")
        return team, project

    def resolve_labels(self, metadata: dict[str, Any], draft: TaskDraft, *, team_id: str | None = None) -> list[str]:
        values = {"type": self._type_label(draft.type), "area": self._area_label(draft.area)}
        if draft.environment:
            values["environment"] = self._environment_label(draft.environment)
        result: list[str] = []
        for group, value in values.items():
            candidates = [item for item in metadata["labels"] if str(item.get("name", "")).casefold() == value.casefold() and self._group_name(item) == group]
            label = next((item for item in candidates if not team_id or not (item.get("team") or {}).get("id") or str((item.get("team") or {}).get("id")) == team_id), None)
            if not label:
                raise LinearError(f"Label Linear não encontrada: {value}.")
            result.append(str(label["id"]))
        return result

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _graphql(self, query: str, variables: dict[str, Any], *, context: str) -> dict[str, Any]:
        if not self._api_key:
            raise LinearError("LINEAR_API_KEY não está configurada.")
        try:
            session = await self._get_session()
            async with session.post(self._endpoint, headers={"Authorization": self._api_key, "Content-Type": "application/json"}, json={"query": query, "variables": variables}) as response:
                body = await response.text()
                if response.status == 401 or response.status == 403:
                    logger.error("Linear authentication failed context=%s status=%s", context, response.status)
                    raise LinearError("Credencial do Linear inválida.")
                if response.status == 429:
                    logger.error("Linear rate limited context=%s", context)
                    raise LinearError("Linear atingiu o limite de requisições. Tente novamente mais tarde.")
                if response.status < 200 or response.status >= 300:
                    logger.error("Linear API failed context=%s status=%s", context, response.status)
                    raise LinearError(f"Linear recusou a requisição ({response.status}).")
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    logger.error("Linear invalid JSON context=%s status=%s", context, response.status)
                    raise LinearError("Resposta inválida do Linear.") from None
                errors = parsed.get("errors") if isinstance(parsed, dict) else None
                if errors:
                    logger.error("Linear GraphQL errors context=%s count=%s", context, len(errors) if isinstance(errors, list) else 1)
                    raise LinearError("O Linear retornou um erro ao processar a requisição.")
                data = parsed.get("data") if isinstance(parsed, dict) else None
                if not isinstance(data, dict):
                    raise LinearError("Resposta inválida do Linear.")
                return data
        except LinearError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError) as error:
            logger.error("Linear connection failed context=%s error=%s", context, type(error).__name__)
            raise LinearError("Não foi possível conectar ao Linear.") from error

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    @staticmethod
    def _nodes(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = payload.get(key, {}) if isinstance(payload, dict) else {}
        nodes = value.get("nodes") if isinstance(value, dict) else []
        return [item for item in nodes if isinstance(item, dict)] if isinstance(nodes, list) else []

    @staticmethod
    def _find_name(items: list[dict[str, Any]], name: str | None) -> dict[str, Any] | None:
        return next((item for item in items if str(item.get("name", "")).casefold() == str(name or "").casefold()), None)

    @staticmethod
    def _find_project(items: list[dict[str, Any]], name: str | None, team_id: str) -> dict[str, Any] | None:
        for item in items:
            if str(item.get("name", "")).casefold() != str(name or "").casefold():
                continue
            team_ids = [str(node.get("id")) for node in ((item.get("teams") or {}).get("nodes") or []) if isinstance(node, dict)]
            if not team_ids or team_id in team_ids:
                return item
        return None

    @staticmethod
    def _find_state(items: list[dict[str, Any]], name: str, team_id: str) -> dict[str, Any] | None:
        return next((item for item in items if str(item.get("name", "")).casefold() == name.casefold() and str((item.get("team") or {}).get("id")) == team_id), None)

    @staticmethod
    def _group_name(item: dict[str, Any]) -> str:
        # Linear models label groups as parent labels (the group itself is not
        # applied to issues); keep the fallback for older/mock payloads.
        return str(((item.get("parent") or item.get("group") or {}).get("name", ""))).casefold()

    @staticmethod
    def _type_label(value: str) -> str:
        return {"bug": "Bug", "feature": "Feature", "improvement": "Improvement", "maintenance": "Maintenance", "research": "Research"}.get(value, "Improvement")

    @staticmethod
    def _area_label(value: str) -> str:
        return {"ayla": "Backend", "api": "Backend", "backend": "Backend", "site": "Frontend", "frontend": "Frontend", "database": "Database", "discord": "Discord", "minecraft": "Minecraft", "network": "Network", "security": "Security", "infrastructure": "DevOps", "devops": "DevOps"}.get(value, "Backend")

    @staticmethod
    def _environment_label(value: str) -> str:
        return {"production": "Production", "staging": "Staging", "local": "Local"}.get(value, value.title())

    @staticmethod
    def _validate(draft: TaskDraft) -> None:
        if not draft.title.strip():
            raise LinearError("O título da issue não pode ficar vazio.")
        if not draft.description.strip():
            raise LinearError("A descrição da issue não pode ficar vazia.")
