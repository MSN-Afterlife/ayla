import asyncio
import json
import unittest

from bot.config import Settings
from bot.services.linear_service import LINEAR_PRIORITY_VALUES, LinearError, LinearService
from bot.services.task_provider import TaskDraft
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

    def test_minecraft_message_routes_to_operations(self):
        self.assertEqual(LinearService.route(TaskDraft("Corrigir /skin", "falha após migração para Velocity")), ("Operations", "Minecraft Server"))

    def test_ambiguous_project_is_left_unassigned(self):
        self.assertEqual(LinearService.route(TaskDraft("Problema", "detalhes insuficientes")), ("Software", None))

    def test_environment_is_omitted_without_evidence(self):
        analysis = _parse_analysis('{"title":"x","description":"y","area":"minecraft"}')
        self.assertIsNone(__import__("bot.services.task_provider", fromlist=["task_draft_from_analysis"]).task_draft_from_analysis(analysis).environment)

    def test_priority_mapping(self):
        self.assertEqual(LINEAR_PRIORITY_VALUES, {"urgent": 1, "high": 2, "normal": 3, "low": 4})

    def test_label_resolution_uses_group_and_omits_unknown_environment(self):
        service = LinearService("token")
        labels = service.resolve_labels(metadata(), TaskDraft("x", "y", type="bug", area="minecraft"))
        self.assertEqual(labels, ["bug", "minecraft-area"])
        with self.assertRaises(LinearError):
            service.resolve_labels(metadata(), TaskDraft("x", "y", type="research", area="minecraft"))


class LinearApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_discovery_is_cached(self):
        service = LinearService("secret-token")
        md = metadata()
        service._session = FakeSession([
            (200, {"data": {"teams": {"nodes": md["teams"]}}}),
            (200, {"data": {"projects": {"nodes": md["projects"]}}}),
            (200, {"data": {"issueLabels": {"nodes": md["labels"]}}}),
            (200, {"data": {"workflowStates": {"nodes": md["states"]}}}),
        ])
        first = await service.get_metadata()
        second = await service.get_metadata()
        self.assertEqual(first, second)
        self.assertEqual(len(service._session.requests), 4)

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


if __name__ == "__main__":
    unittest.main()
