import asyncio
import json
import logging
import re
import time
from typing import Any

import aiohttp

from bot.services.task_provider import TaskDraft

logger = logging.getLogger(__name__)
LINEAR_API_URL = "https://api.linear.app/graphql"
LINEAR_PRIORITY_VALUES = {"urgent": 1, "high": 2, "normal": 3, "low": 4}
_TOKEN_RE = re.compile(r"(?<![\w/])([\wÀ-ÿ/-]+)(?![\w])", re.IGNORECASE)
_GRAPHQL_LOG_TEXT_LIMIT = 500


class LinearError(Exception):
    """Safe provider error; never contains credentials or raw API responses."""


class LinearService:
    def __init__(self, api_key: str | None, *, timeout_seconds: float = 15, cache_seconds: int = 600,
                 endpoint: str = LINEAR_API_URL, create_subissues: bool = False, max_subissues: int = 5,
                 use_estimates: bool = False, use_assignee: bool = False, use_due_date: bool = False,
                 use_cycles: bool = False, use_milestones: bool = False) -> None:
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._cache_seconds = cache_seconds
        self._endpoint = endpoint
        self._session: aiohttp.ClientSession | None = None
        self._metadata: dict[str, Any] | None = None
        self._metadata_cached_at = 0.0
        self._create_subissues = create_subissues
        self._max_subissues = max(0, min(10, max_subissues))
        self._use_estimates = use_estimates
        self._use_assignee = use_assignee
        self._use_due_date = use_due_date
        self._use_cycles = use_cycles
        self._use_milestones = use_milestones

    async def create_task(self, draft: TaskDraft, *, context: str = "task") -> dict[str, Any]:
        self._validate(draft)
        metadata = await self.get_metadata()
        team_name, project_name = self.route(draft)
        team = self._find_name(metadata["teams"], team_name)
        if not team:
            raise LinearError(f"Team Linear não encontrado: {team_name}.")
        project = self._find_project(metadata["projects"], project_name, str(team["id"]))
        if project_name and not project:
            raise LinearError(f"Projeto Linear não encontrado: {project_name}.")
        input_data = self._issue_input(metadata, draft, team, project)
        parent = await self._create_issue({"input": input_data}, context=context)
        result = dict(parent, subissues=[], subissue_failures=[])
        if self._create_subissues and draft.subtasks:
            for index, subtask in enumerate(draft.subtasks[:self._max_subissues], start=1):
                try:
                    child = dict(input_data, title=subtask.title[:200], description=subtask.description[:12000], parentId=parent.get("id"))
                    result["subissues"].append(await self._create_issue({"input": child}, context=f"{context} subissue {index}"))
                except LinearError as error:
                    logger.error("Linear subissue creation failed parent_id=%s index=%s error=%s", parent.get("id", "-"), index, error)
                    result["subissue_failures"].append({"index": index, "title": subtask.title, "error": str(error)})
        return result

    async def _create_issue(self, variables: dict[str, Any], *, context: str) -> dict[str, Any]:
        payload = await self._graphql("""mutation IssueCreate($input: IssueCreateInput!) {
            issueCreate(input: $input) { success issue { id identifier url title } }
        }""", variables, context=context)
        result = payload.get("issueCreate") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or result.get("success") is not True or not isinstance(result.get("issue"), dict):
            raise LinearError("Linear não confirmou a criação da issue.")
        issue = result["issue"]
        logger.info("Linear issue created context=%s issue_id=%s identifier=%s", context, issue.get("id", "-"), issue.get("identifier", "-"))
        return issue

    def _issue_input(self, metadata: dict[str, Any], draft: TaskDraft, team: dict[str, Any], project: dict[str, Any] | None) -> dict[str, Any]:
        data: dict[str, Any] = {"teamId": team["id"], "title": draft.title[:200], "description": draft.description[:12000], "priority": LINEAR_PRIORITY_VALUES.get((draft.priority or "none").casefold(), 0), "labelIds": self.resolve_labels(metadata, draft, team_id=str(team["id"]))}
        if project:
            data["projectId"] = project["id"]
        state_name = draft.status or ("Backlog" if draft.manual_triage and self._find_state(metadata["states"], "Backlog", str(team["id"])) else "Todo")
        state = self._find_state(metadata["states"], state_name, str(team["id"]))
        if state:
            data["stateId"] = state["id"]
        uncertain = draft.manual_triage or draft.confidence.casefold() == "low" or str(draft.metadata.get("destination", "")).casefold() == "manual_triage" or str(draft.metadata.get("confidence", "")).casefold() == "low"
        if not uncertain and self._use_estimates and self._valid_estimate(metadata, team, draft.estimate):
            data["estimate"] = draft.estimate
        if not uncertain and self._use_assignee:
            member = self._find_member(metadata.get("members", []), draft.assignee)
            if member:
                data["assigneeId"] = member["id"]
        if not uncertain and self._use_due_date and draft.due_date:
            data["dueDate"] = draft.due_date.isoformat()
        if not uncertain and self._use_cycles:
            cycle = self._find_scoped(metadata.get("cycles", []), draft.cycle, str(team["id"]))
            if cycle:
                data["cycleId"] = cycle["id"]
        if not uncertain and self._use_milestones and project:
            milestone = self._find_milestone(metadata.get("milestones", []), draft.milestone, str(project["id"]))
            if milestone:
                data["projectMilestoneId"] = milestone["id"]
        return data

    async def get_metadata(self) -> dict[str, Any]:
        if self._metadata is not None and time.monotonic() - self._metadata_cached_at < self._cache_seconds:
            return self._metadata
        teams = await self._graphql("query { teams(first: 250) { nodes { id name key cyclesEnabled defaultIssueEstimate timezone triageEnabled } } }", {}, context="teams")
        # Fetch milestones separately. Nesting up to 250 milestones below each of
        # 250 projects exceeds Linear's query-complexity limit, even for small
        # workspaces, because complexity is calculated from requested bounds.
        projects = await self._graphql("query { projects(first: 250) { nodes { id name teams(first: 10) { nodes { id } } } } }", {}, context="projects")
        milestones = await self._graphql("query { projectMilestones(first: 250) { nodes { id name targetDate project { id } } } }", {}, context="milestones")
        labels = await self._graphql("query { issueLabels(first: 250) { nodes { id name isGroup parent { name } team { id } } } }", {}, context="labels")
        states = await self._graphql("query { workflowStates(first: 250) { nodes { id name type team { id } } } }", {}, context="states")
        metadata: dict[str, Any] = {"teams": self._nodes(teams, "teams"), "projects": self._nodes(projects, "projects"), "labels": self._nodes(labels, "issueLabels"), "states": self._nodes(states, "workflowStates"), "members": [], "cycles": [], "milestones": []}
        metadata["milestones"] = self._nodes(milestones, "projectMilestones")
        if not metadata["teams"]:
            raise LinearError("Linear não retornou teams.")
        if self._use_assignee:
            try:
                users = await self._graphql("query { users(first: 250) { nodes { id name displayName email active } } }", {}, context="members")
                metadata["members"] = self._nodes(users, "users")
            except LinearError as error:
                logger.warning("Linear optional members metadata unavailable: %s", error)
        if self._use_cycles:
            try:
                cycles = await self._graphql("query { cycles(first: 250) { nodes { id name number team { id } startsAt endsAt isActive isFuture isPast } } }", {}, context="cycles")
                metadata["cycles"] = self._nodes(cycles, "cycles")
            except LinearError as error:
                logger.warning("Linear optional cycles metadata unavailable: %s", error)
        self._metadata, self._metadata_cached_at = metadata, time.monotonic()
        logger.info("Linear metadata refreshed teams=%s projects=%s labels=%s states=%s members=%s cycles=%s milestones=%s", *(len(metadata[key]) for key in ("teams", "projects", "labels", "states", "members", "cycles", "milestones")))
        return metadata

    def invalidate_metadata(self) -> None:
        self._metadata = None
        self._metadata_cached_at = 0.0

    @staticmethod
    def route(draft: TaskDraft) -> tuple[str, str | None]:
        text = f"{draft.title} {draft.source_content}".casefold()
        area = (draft.area or "other").casefold()
        manual = draft.manual_triage or str(draft.metadata.get("destination", "")).casefold() == "manual_triage" or draft.confidence.casefold() == "low" or str(draft.metadata.get("confidence", "")).casefold() == "low"
        tokens = {item.casefold() for item in _TOKEN_RE.findall(text)}
        project = None if manual else draft.project
        if not project and not manual:
            if area == "minecraft" or {"minecraft", "velocity"} & tokens or "/skin" in text:
                project = "Minecraft Server"
            elif area == "discord" or ("discord" in tokens and any(word in text for word in ("canal", "cargo", "permiss", "moder", "role"))):
                project = "Discord Server"
            elif area in {"infrastructure", "network", "devops"} or {"vps", "infraestrutura", "datacenter"} & tokens:
                project = "Infraestrutura"
            elif area == "site" or {"site", "frontend"} & tokens:
                project = "Site"
            elif area in {"ayla", "api", "backend", "database", "security"} or "ayla" in tokens:
                project = "Ayla"
        team = draft.team or ("Operations" if project in {"Minecraft Server", "Discord Server", "Infraestrutura"} or area in {"minecraft", "discord", "infrastructure", "network", "devops"} else "Software")
        return team, project

    def resolve_labels(self, metadata: dict[str, Any], draft: TaskDraft, *, team_id: str | None = None) -> list[str]:
        values = [("type", self._type_label(draft.type)), ("area", self._area_label(draft.area))]
        if draft.environment:
            values.append(("environment", self._environment_label(draft.environment)))
        result: list[str] = []
        for group, value in values:
            if not value:
                continue
            candidates = [item for item in metadata.get("labels", []) if str(item.get("name", "")).casefold() == value.casefold() and self._group_name(item) == group]
            label = next((item for item in candidates if not team_id or not (item.get("team") or {}).get("id") or str((item.get("team") or {}).get("id")) == team_id), None)
            if label:
                result.append(str(label["id"]))
            else:
                logger.warning("Linear label unavailable group=%s value=%s; skipping optional label", group, value)
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
                if response.status in {401, 403}:
                    logger.error("Linear authentication failed context=%s status=%s", context, response.status)
                    raise LinearError("Credencial do Linear inválida.")
                if response.status == 429:
                    logger.error("Linear rate limited context=%s", context)
                    raise LinearError("Linear atingiu o limite de requisições. Tente novamente mais tarde.")
                if response.status < 200 or response.status >= 300:
                    self._log_graphql_errors(body, context=context, status=response.status)
                    raise LinearError(f"Linear recusou a requisição ({response.status}).")
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    logger.error("Linear invalid JSON context=%s status=%s", context, response.status)
                    raise LinearError("Resposta inválida do Linear.") from None
                if isinstance(parsed, dict) and parsed.get("errors"):
                    self._log_graphql_errors(parsed, context=context, status=response.status)
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

    def _log_graphql_errors(self, payload: str | dict[str, Any], *, context: str, status: int) -> None:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if not isinstance(errors, list) or not errors:
            logger.error("Linear API failed context=%s status=%s graphql_errors=unavailable", context, status)
            return
        for error in errors:
            if not isinstance(error, dict):
                continue
            message = self._safe_log_text(error.get("message"))
            extensions = error.get("extensions") if isinstance(error.get("extensions"), dict) else {}
            code = self._safe_log_text(extensions.get("code"))
            logger.error(
                "Linear GraphQL error context=%s status=%s graphql_error=%r graphql_code=%r path=%r locations=%r",
                context, status, message, code, error.get("path"), error.get("locations"),
            )

    def _safe_log_text(self, value: Any) -> str:
        text = " ".join(str(value or "").split())
        if self._api_key:
            text = text.replace(self._api_key, "[REDACTED]")
        return text[:_GRAPHQL_LOG_TEXT_LIMIT]

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
        if not name:
            return None
        for item in items:
            if str(item.get("name", "")).casefold() == name.casefold():
                ids = [str(node.get("id")) for node in ((item.get("teams") or {}).get("nodes") or []) if isinstance(node, dict)]
                if not ids or team_id in ids:
                    return item
        return None

    @staticmethod
    def _find_state(items: list[dict[str, Any]], name: str, team_id: str) -> dict[str, Any] | None:
        return next((item for item in items if str(item.get("name", "")).casefold() == name.casefold() and str((item.get("team") or {}).get("id")) == team_id), None)

    @staticmethod
    def _group_name(item: dict[str, Any]) -> str:
        return str(((item.get("parent") or item.get("group") or {}).get("name", ""))).casefold()

    @staticmethod
    def _type_label(value: str) -> str:
        return {"bug": "Bug", "feature": "Feature", "improvement": "Improvement", "maintenance": "Maintenance", "research": "Research"}.get(value, "")

    @staticmethod
    def _area_label(value: str) -> str:
        return {"ayla": "Backend", "api": "Backend", "backend": "Backend", "site": "Frontend", "frontend": "Frontend", "database": "Database", "discord": "Discord", "minecraft": "Minecraft", "network": "Network", "security": "Security", "devops": "DevOps"}.get(value, "")

    @staticmethod
    def _environment_label(value: str) -> str:
        return {"production": "Production", "staging": "Staging", "local": "Local"}.get(value, value.title())

    @staticmethod
    def _find_member(items: list[dict[str, Any]], value: str | None) -> dict[str, Any] | None:
        needle = str(value or "").strip().casefold()
        return next((item for item in items if needle and needle in {str(item.get(key, "")).strip().casefold() for key in ("id", "name", "displayName", "email")}), None)

    @staticmethod
    def _find_scoped(items: list[dict[str, Any]], name: str | None, team_id: str) -> dict[str, Any] | None:
        return next((item for item in items if str(item.get("name", "")).casefold() == str(name or "").casefold() and str((item.get("team") or {}).get("id")) == team_id), None)

    @staticmethod
    def _find_milestone(items: list[dict[str, Any]], name: str | None, project_id: str) -> dict[str, Any] | None:
        return next((item for item in items if str(item.get("name", "")).casefold() == str(name or "").casefold() and str((item.get("project") or {}).get("id")) == project_id), None)

    @staticmethod
    def _valid_estimate(metadata: dict[str, Any], team: dict[str, Any], value: int | None) -> bool:
        allowed = team.get("estimateValues") or metadata.get("estimate_values")
        return value is not None and value >= 0 and isinstance(allowed, list) and value in allowed

    @staticmethod
    def _validate(draft: TaskDraft) -> None:
        if not draft.title.strip():
            raise LinearError("O título da issue não pode ficar vazio.")
        if not draft.description.strip():
            raise LinearError("A descrição da issue não pode ficar vazia.")
