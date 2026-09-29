import asyncio
import json
import logging
import unittest
from datetime import date

from bot.config import Settings
from bot.services.linear_service import LINEAR_PRIORITY_VALUES, LinearError, LinearService
from bot.services.task_provider import TaskDraft, task_draft_from_analysis
from bot.services.task_ai_service import TaskSubtask
from bot.services.task_ai_service import _parse_analysis


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def text(self):
        return self._body


class FakeSession:
    closed = False

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def post(self, endpoint, **kwargs):
        self.requests.append((endpoint, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(*response)

    async def close(self):
        self.closed = True


def metadata():
    return {
        "teams": [{"id": "software", "name": "Software"}, {"id": "ops", "name": "Operations"}],
        "projects": [
            {"id": "ayla", "name": "Ayla", "teams": {"nodes": [{"id": "software"}]}},
            {"id": "site", "name": "Site", "teams": {"nodes": [{"id": "software"}]}},
            {"id": "minecraft", "name": "Minecraft Server", "teams": {"nodes": [{"id": "ops"}]}},
            {"id": "discord", "name": "Discord Server", "teams": {"nodes": [{"id": "ops"}]}},
            {"id": "infra", "name": "Infraestrutura", "teams": {"nodes": [{"id": "ops"}]}},
        ],
        "labels": [
            {"id": "bug", "name": "Bug", "parent": {"name": "Type"}},
            {"id": "feature", "name": "Feature", "parent": {"name": "Type"}},
            {"id": "backend", "name": "Backend", "parent": {"name": "Area"}},
            {"id": "minecraft-area", "name": "Minecraft", "parent": {"name": "Area"}},
            {"id": "prod", "name": "Production", "parent": {"name": "Environment"}},
        ],
        "states": [{"id": "todo", "name": "Todo", "team": {"id": "software"}}],
    }


class LinearRoutingTests(unittest.TestCase):
    def test_linear_enabled_defaults_to_false(self):
        self.assertFalse(Settings("x").linear_enabled)

    def test_requested_routes(self):
        cases = {
            "Ayla": ("Software", "Ayla"), "Site": ("Software", "Site"),
            "Minecraft Server": ("Operations", "Minecraft Server"),
            "Discord Server": ("Operations", "Discord Server"),
            "Infraestrutura": ("Operations", "Infraestrutura"),
        }
        for project, expected in cases.items():
            area = {"Ayla": "ayla", "Site": "site", "Minecraft Server": "minecraft", "Discord Server": "discord", "Infraestrutura": "infrastructure"}[project]
            self.assertEqual(LinearService.route(TaskDraft("x", "x", area=area)), expected)

    def test_staging_ambiguous_manual_triage_routes_operations_without_project(self):
        draft = TaskDraft(
            "staging morreu ao vivo", "descrição com **Servidor:** Discord",
            area="infrastructure", environment="staging",
            metadata={"destination": "manual_triage", "confidence": "low"},
            source_content="staging morreu ao vivo",
        )
        self.assertEqual(LinearService.route(draft), ("Operations", None))

    def test_discord_origin_does_not_imply_discord_project(self):
        draft = TaskDraft(
            "staging morreu ao vivo", "**Canal:** #bugs **Servidor:** Ayla Discord",
            area="infrastructure", source_content="staging morreu ao vivo",
        )
        self.assertEqual(LinearService.route(draft), ("Operations", "Infraestrutura"))

    def test_explicit_discord_problem_routes_discord_server(self):
        draft = TaskDraft("Permissões do servidor Discord", "cargo não consegue acessar o canal", area="discord", source_content="Permissões do servidor Discord")
        self.assertEqual(LinearService.route(draft), ("Operations", "Discord Server"))

    def test_explicit_minecraft_problem_routes_minecraft_server(self):
        self.assertEqual(LinearService.route(TaskDraft("/skin quebrada", "falha no Minecraft", area="minecraft", source_content="falha no Minecraft")), ("Operations", "Minecraft Server"))

    def test_explicit_infrastructure_routes_infrastructure_project(self):
        self.assertEqual(LinearService.route(TaskDraft("VPS fora", "rede da VPS indisponível", area="infrastructure", source_content="rede da VPS indisponível")), ("Operations", "Infraestrutura"))

    def test_ayla_and_site_routes(self):
        self.assertEqual(LinearService.route(TaskDraft("Comando Ayla", "bug no bot", area="ayla", source_content="bug no bot Ayla")), ("Software", "Ayla"))
        self.assertEqual(LinearService.route(TaskDraft("Página quebrada", "erro no site", area="site", source_content="erro no site")), ("Software", "Site"))

    def test_minecraft_message_routes_to_operations(self):
        self.assertEqual(LinearService.route(TaskDraft("Corrigir /skin", "falha após migração para Velocity")), ("Operations", "Minecraft Server"))

    def test_ambiguous_project_is_left_unassigned(self):
        self.assertEqual(LinearService.route(TaskDraft("Problema", "detalhes insuficientes")), ("Software", None))

    def test_environment_is_omitted_without_evidence(self):
        analysis = _parse_analysis('{"title":"x","description":"y","area":"minecraft"}')
        self.assertIsNone(__import__("bot.services.task_provider", fromlist=["task_draft_from_analysis"]).task_draft_from_analysis(analysis).environment)

    def test_priority_mapping(self):
        self.assertEqual(LINEAR_PRIORITY_VALUES, {"urgent": 1, "high": 2, "normal": 3, "low": 4})

    def test_source_url_and_metadata_never_contaminate_routing(self):
        draft = TaskDraft("staging caiu", "https://discord.com/channels/1/2/3", area="infrastructure", source_content="staging caiu", source_url="https://discord.com/channels/1/2/3", source_metadata={"guild": "Discord Server"})
        self.assertEqual(LinearService.route(draft), ("Operations", "Infraestrutura"))

    def test_low_confidence_does_not_select_project_or_advanced_scope(self):
        draft = TaskDraft("problema", "sem contexto", area="infrastructure", confidence="low", milestone="M1", assignee="someone")
        self.assertEqual(LinearService.route(draft), ("Operations", None))

    def test_manual_triage_generic_area_without_component_evidence_stays_unset(self):
        draft = TaskDraft("staging morreu ao vivo", "", area="infrastructure", manual_triage=True, field_confidence={"area": "medium"})
        self.assertEqual(LinearService.route(draft), ("Operations", None))

    def test_manual_triage_preserves_independently_confident_site_project(self):
        draft = TaskDraft("site", "500", team="Software", project="Site", area="api", manual_triage=True, confidence="medium", source_content="O site em staging retorna 500", field_confidence={"team": "high", "project": "high", "area": "medium"})
        self.assertEqual(LinearService.route(draft), ("Software", "Site"))

    def test_manual_triage_medium_project_requires_direct_evidence(self):
        direct = TaskDraft("site", "500", project="Site", manual_triage=True, source_content="O site retorna 500", field_confidence={"project": "medium"})
        ambiguous = TaskDraft("staging morreu", "", project="Site", manual_triage=True, source_content="staging morreu", field_confidence={"project": "medium"})
        self.assertEqual(LinearService.route(direct), ("Software", "Site"))
        self.assertEqual(LinearService.route(ambiguous), ("Software", None))

    def test_manual_triage_low_project_confidence_unsets_project(self):
        draft = TaskDraft("site", "ambiguous", team="Software", project="Site", manual_triage=True, field_confidence={"project": "low"})
        self.assertEqual(LinearService.route(draft), ("Software", None))

    def test_analysis_to_draft_transports_native_fields(self):
        analysis = _parse_analysis('{"title":"site","description":"500","project":"Site","team":"Software","area":"api","environment":"staging","due_date":"2026-10-02","field_confidence":{"project":"high","due_date":"high"}}', {"manual_triage"})
        draft = task_draft_from_analysis(analysis, source_content="O site em staging")
        self.assertEqual((draft.team, draft.project, draft.environment, draft.due_date.isoformat()), ("Software", "Site", "staging", "2026-10-02"))
        self.assertEqual(draft.field_confidence["due_date"], "high")

    def test_label_resolution_uses_group_and_omits_unknown_environment(self):
        service = LinearService("token")
        labels = service.resolve_labels(metadata(), TaskDraft("x", "y", type="bug", area="minecraft"))
        self.assertEqual(labels, ["bug", "minecraft-area"])
        self.assertEqual(service.resolve_labels(metadata(), TaskDraft("x", "y", type="research", area="minecraft")), ["minecraft-area"])


class LinearApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_discovery_is_cached(self):
        service = LinearService("secret-token")
        md = metadata()
        service._session = FakeSession([
            (200, {"data": {"teams": {"nodes": md["teams"]}}}),
            (200, {"data": {"projects": {"nodes": md["projects"]}}}),
            (200, {"data": {"projectMilestones": {"nodes": []}}}),
            (200, {"data": {"issueLabels": {"nodes": md["labels"]}}}),
            (200, {"data": {"workflowStates": {"nodes": md["states"]}}}),
        ])
        first = await service.get_metadata()
        second = await service.get_metadata()
        self.assertEqual(first, second)
        self.assertEqual(len(service._session.requests), 5)
        project_query = service._session.requests[1][1]["json"]["query"]
        milestone_query = service._session.requests[2][1]["json"]["query"]
        self.assertNotIn("projectMilestones", project_query)
        self.assertIn("teams(first: 10)", project_query)
        self.assertIn("projectMilestones(first: 250)", milestone_query)

    async def test_metadata_and_issue_creation_use_variables(self):
        service = LinearService("secret-token")
        md = metadata()
        service._metadata = md
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service._session = FakeSession([(200, {"data": {"issueCreate": {"success": True, "issue": {"id": "i1", "identifier": "AY-1", "url": "https://linear.test/AY-1"}}}})])
        issue = await service.create_task(TaskDraft("Corrigir /skin", "descrição", priority="high", area="minecraft", type="bug", environment="production"))
        self.assertEqual(issue["identifier"], "AY-1")
        payload = service._session.requests[0][1]["json"]
        self.assertNotIn("secret-token", json.dumps(payload))
        self.assertEqual(payload["variables"]["input"]["priority"], 2)
        self.assertEqual(payload["variables"]["input"]["projectId"], "minecraft")
        self.assertEqual(payload["variables"]["input"]["labelIds"], ["bug", "minecraft-area", "prod"])

    async def test_graphql_errors_are_safe(self):
        service = LinearService("secret-token")
        service._session = FakeSession([(200, {"errors": [{"message": "private detail"}]})])
        with self.assertRaisesRegex(LinearError, "erro ao processar") as ctx:
            await service._graphql("query { x }", {}, context="test")
        self.assertNotIn("private detail", str(ctx.exception))

    async def test_graphql_error_details_are_logged_for_http_400(self):
        service = LinearService("secret-token")
        service._session = FakeSession([(400, {"errors": [{
            "message": "Query too complex secret-token", "path": ["projects"],
            "locations": [{"line": 1, "column": 9}],
            "extensions": {"code": "INPUT_ERROR"},
        }]})])
        with self.assertLogs("bot.services.linear_service", logging.ERROR) as logs:
            with self.assertRaisesRegex(LinearError, r"recusou a requisição \(400\)"):
                await service._graphql("query { projects { nodes { id } } }", {}, context="projects")
        output = "\n".join(logs.output)
        self.assertIn("graphql_error='Query too complex [REDACTED]'", output)
        self.assertIn("graphql_code='INPUT_ERROR'", output)
        self.assertIn("path=['projects']", output)
        self.assertNotIn("secret-token", output)

    async def test_auth_timeout_rate_limit_and_invalid_json(self):
        for status, body, expected in [
            (401, {}, "Credencial"), (429, {}, "limite"), (200, {"bad": True}, "inválida"),
        ]:
            service = LinearService("secret-token")
            service._session = FakeSession([(status, body)])
            with self.assertRaisesRegex(LinearError, expected):
                await service._graphql("query { x }", {}, context="test")
        service = LinearService("secret-token")
        service._session = FakeSession([asyncio.TimeoutError()])
        with self.assertRaisesRegex(LinearError, "conectar"):
            await service._graphql("query { x }", {}, context="test")

    async def test_missing_project_and_label_fail_before_mutation(self):
        service = LinearService("secret-token")
        service._metadata = metadata()
        service._metadata_cached_at = asyncio.get_running_loop().time()
        with self.assertRaisesRegex(LinearError, "Projeto"):
            await service.create_task(TaskDraft("x", "y", project="does-not-exist"))

    async def test_no_key_is_disabled_safely(self):
        with self.assertRaisesRegex(LinearError, "LINEAR_API_KEY"):
            await LinearService(None).get_metadata()

    async def test_advanced_fields_use_real_scoped_metadata(self):
        service = LinearService("secret-token", use_estimates=True, use_assignee=True, use_due_date=True, use_cycles=True, use_milestones=True)
        md = metadata()
        md["teams"][1]["estimateValues"] = [1, 2, 3, 5, 8]
        md["members"] = [{"id": "u1", "name": "Ana", "displayName": "Ana", "email": "ana@example.test"}]
        md["cycles"] = [{"id": "c1", "name": "Sprint 1", "team": {"id": "ops"}}]
        md["milestones"] = [{"id": "m1", "name": "Outage", "project": {"id": "infra"}}]
        md["states"].append({"id": "ops-todo", "name": "Todo", "team": {"id": "ops"}})
        service._metadata = md
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service._session = FakeSession([(200, {"data": {"issueCreate": {"success": True, "issue": {"id": "i1", "identifier": "OPS-1", "url": "https://linear.test/OPS-1"}}}})])
        draft = TaskDraft("VPS", "desc", area="infrastructure", type="bug", environment="staging", estimate=5, assignee="ana@example.test", due_date=date(2026, 10, 3), cycle="Sprint 1", milestone="Outage")
        await service.create_task(draft)
        data = service._session.requests[0][1]["json"]["variables"]["input"]
        self.assertEqual(data["estimate"], 5)
        self.assertEqual(data["assigneeId"], "u1")
        self.assertEqual(data["dueDate"], "2026-10-03")
        self.assertEqual(data["cycleId"], "c1")
        self.assertEqual(data["projectMilestoneId"], "m1")

    async def test_advanced_fields_are_omitted_by_default(self):
        service = LinearService("secret-token")
        service._metadata = metadata()
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service._session = FakeSession([(200, {"data": {"issueCreate": {"success": True, "issue": {"id": "i1", "identifier": "OPS-1"}}}})])
        await service.create_task(TaskDraft("x", "y", area="infrastructure", estimate=5, assignee="u1", due_date=date(2026, 10, 3), cycle="Sprint 1", milestone="M1"))
        data = service._session.requests[0][1]["json"]["variables"]["input"]
        for key in ("estimate", "assigneeId", "dueDate", "cycleId", "projectMilestoneId"):
            self.assertNotIn(key, data)

    async def test_manual_triage_suppresses_all_optional_scope_fields(self):
        service = LinearService("secret-token", use_estimates=True, use_assignee=True, use_due_date=True, use_cycles=True, use_milestones=True)
        md = metadata()
        md["teams"][1]["estimateValues"] = [5]
        md["members"] = [{"id": "u1", "name": "Ana"}]
        md["cycles"] = [{"id": "c1", "name": "Sprint 1", "team": {"id": "ops"}}]
        md["milestones"] = [{"id": "m1", "name": "Outage", "project": {"id": "infra"}}]
        md["states"].append({"id": "ops-todo", "name": "Todo", "team": {"id": "ops"}})
        service._metadata = md
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service._session = FakeSession([(200, {"data": {"issueCreate": {"success": True, "issue": {"id": "i1"}}}})])
        draft = TaskDraft("triage", "desc", area="infrastructure", manual_triage=True, confidence="low", estimate=5, assignee="Ana", due_date=date(2026, 10, 3), cycle="Sprint 1", milestone="Outage")
        await service.create_task(draft)
        data = service._session.requests[0][1]["json"]["variables"]["input"]
        for key in ("projectId", "estimate", "assigneeId", "dueDate", "cycleId", "projectMilestoneId"):
            self.assertNotIn(key, data)

    async def test_manual_triage_keeps_high_confidence_project_and_due_date(self):
        service = LinearService("secret-token", use_due_date=True)
        md = metadata()
        md["states"].append({"id": "software-backlog", "name": "Backlog", "team": {"id": "software"}})
        service._metadata = md
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service._session = FakeSession([(200, {"data": {"issueCreate": {"success": True, "issue": {"id": "i1"}}}})])
        draft = TaskDraft("site", "desc", team="Software", project="Site", area="api", manual_triage=True, confidence="medium", due_date=date(2026, 10, 2), field_confidence={"team": "high", "project": "high", "due_date": "high"})
        await service.create_task(draft)
        data = service._session.requests[0][1]["json"]["variables"]["input"]
        self.assertEqual(data["projectId"], "site")
        self.assertEqual(data["dueDate"], "2026-10-02")

    async def test_parent_and_real_subissues_have_parent_id_and_partial_failure_is_reported(self):
        service = LinearService("secret-token", create_subissues=True, max_subissues=2)
        service._metadata = metadata()
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service._session = FakeSession([
            (200, {"data": {"issueCreate": {"success": True, "issue": {"id": "parent", "identifier": "OPS-1"}}}}),
            (200, {"data": {"issueCreate": {"success": True, "issue": {"id": "child", "identifier": "OPS-2"}}}}),
            (200, {"errors": [{"message": "child failed"}]}),
        ])
        result = await service.create_task(TaskDraft("parent", "desc", area="infrastructure", subtasks=[TaskSubtask("one", "one"), TaskSubtask("two", "two"), TaskSubtask("three", "three")]))
        self.assertEqual(len(result["subissues"]), 1)
        self.assertEqual(len(result["subissue_failures"]), 1)
        child_input = service._session.requests[1][1]["json"]["variables"]["input"]
        self.assertEqual(child_input["parentId"], "parent")
        self.assertEqual(len(service._session.requests), 3)

    async def test_metadata_invalidation_forces_refresh(self):
        service = LinearService("secret-token")
        service._metadata = metadata()
        service._metadata_cached_at = asyncio.get_running_loop().time()
        service.invalidate_metadata()
        self.assertIsNone(service._metadata)


if __name__ == "__main__":
    unittest.main()
