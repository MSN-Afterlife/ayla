import asyncio
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from bot.commands.clickup import _custom_fields, resolve_destination
from bot.config import Settings, _load_destination_map, validate_clickup_settings
from bot.services.clickup_service import CLICKUP_PRIORITY_VALUES, ClickUpService, priority_to_clickup
from bot.services.task_ai_service import _deterministic_overrides, _parse_analysis, _parse_subtasks, priority_from_content


class FakeResponse:
    def __init__(self, status: int, body: dict):
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

    def request(self, method, endpoint, **kwargs):
        self.requests.append((method, endpoint, kwargs))
        return FakeResponse(*next(self.responses))

    async def close(self):
        self.closed = True


class TaskTriageTests(unittest.TestCase):
    def test_all_native_priorities_are_numeric(self):
        self.assertEqual(CLICKUP_PRIORITY_VALUES, {"urgent": 1, "high": 2, "normal": 3, "low": 4})
        for semantic, numeric in CLICKUP_PRIORITY_VALUES.items():
            self.assertEqual(priority_to_clickup(semantic), numeric)

    def test_deterministic_priority_overrides(self):
        self.assertEqual(priority_from_content("token exposto no servidor"), "urgent")
        self.assertEqual(_deterministic_overrides("função importante quebrada em produção", "low", "low", "production")[0], "high")
        self.assertEqual(_deterministic_overrides("erro apenas visual de CSS", "high", "low", "unknown")[0], "low")
        self.assertEqual(_deterministic_overrides("erro somente em staging", "urgent", "low", "staging")[0], "normal")

    def test_one_valid_subtask_is_preserved(self):
        subtasks, discarded = _parse_subtasks([{"title": "Validar endpoint /audit", "description": "Consultar o endpoint e confirmar o status HTTP.", "type": "validation"}])
        self.assertEqual(len(subtasks), 1)
        self.assertEqual(discarded, [])

    def test_multiple_subtasks_and_invalid_item(self):
        subtasks, discarded = _parse_subtasks([
            {"title": "Reproduzir erro no endpoint /audit", "description": "Executar a requisição com dados reais.", "type": "investigation"},
            {"title": "", "description": "sem título"},
            {"title": "Validar resposta do endpoint /audit", "description": "Confirmar status e payload após a correção.", "type": "validation"},
        ])
        self.assertEqual(len(subtasks), 2)
        self.assertTrue(discarded)

    def test_invalid_ai_response_is_rejected(self):
        with self.assertRaises(Exception):
            _parse_analysis("não é json")

    def test_destination_is_allowed_or_explicitly_triage(self):
        payload = {"title": "Corrigir site", "description": "Erro no site", "destination": "site_bugs"}
        self.assertEqual(_parse_analysis(json.dumps(payload), {"site_bugs", "manual_triage"}).destination, "site_bugs")
        payload["destination"] = "lista_inventada"
        self.assertEqual(_parse_analysis(json.dumps(payload), {"site_bugs", "manual_triage"}).destination, "manual_triage")

    def test_ai_can_select_a_discovered_list_id(self):
        payload = {"title": "Corrigir site", "description": "Erro no site", "destination": "list-42"}
        self.assertEqual(_parse_analysis(json.dumps(payload), {"list-42"}).destination, "list-42")

    def test_ai_numeric_discovered_list_id_is_normalized(self):
        payload = {"title": "Corrigir site", "description": "Erro no site", "destination": 901316877187}
        self.assertEqual(_parse_analysis(json.dumps(payload), {"901316877187"}).destination, "901316877187")

    def test_destination_configuration_is_validated(self):
        settings = Settings("x", clickup_api_token="token", clickup_destinations={"manual_triage": "123"})
        with self.assertRaises(RuntimeError):
            validate_clickup_settings(settings)

    def test_destination_resolution(self):
        settings = Settings("x", clickup_destinations={"site_bugs": "456"})
        self.assertEqual(resolve_destination(settings, "site_bugs"), "456")
        with self.assertRaises(Exception):
            resolve_destination(settings, "unknown")
        legacy = Settings("x", clickup_list_id="789")
        self.assertEqual(resolve_destination(legacy, "manual_triage"), "789")

    def test_destination_json_rejects_empty_and_unknown_values(self):
        with patch.dict("os.environ", {"CLICKUP_DESTINATIONS": '{"manual_triage":""}'}, clear=False):
            with self.assertRaises(RuntimeError):
                _load_destination_map("CLICKUP_DESTINATIONS")

    def test_custom_fields_use_real_ids_and_dropdown_option_ids(self):
        settings = Settings("token", clickup_custom_field_ids={"risk": "field-risk", "possible_solution": "field-solution"})
        analysis = _parse_analysis(json.dumps({"title": "x", "description": "y", "risk": "critical", "possible_solution": "Criar serviço centralizado."}))
        fields = _custom_fields(analysis, settings, [
            {"id": "field-risk", "type": "drop_down", "type_config": {"options": [{"id": "opt-critical", "name": "Crítico"}]}},
            {"id": "field-solution", "type": "text", "type_config": {}},
        ])
        self.assertEqual(fields, [{"id": "field-risk", "value": "opt-critical"}, {"id": "field-solution", "value": "Criar serviço centralizado."}])

    def test_custom_fields_absent_are_empty(self):
        settings = Settings("token")
        analysis = _parse_analysis('{"title":"x","description":"y"}')
        self.assertEqual(_custom_fields(analysis, settings), [])

    def test_custom_fields_are_discovered_by_name_without_configuration(self):
        settings = Settings("token")
        analysis = _parse_analysis('{"title":"x","description":"y","risk":"critical"}')
        fields = _custom_fields(analysis, settings, [{"id": "field-risk", "name": "Risco", "type": "text"}])
        self.assertEqual(fields, [{"id": "field-risk", "value": "Crítico"}])


class ClickUpPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_clickup_catalog_discovers_workspace_space_folder_and_lists(self):
        service = ClickUpService("secret-token")
        session = FakeSession([
            (200, {"teams": [{"id": "workspace-1", "name": "Meu Workspace"}]}),
            (200, {"spaces": [{"id": "space-1", "name": "Produto"}]}),
            (200, {"folders": [{"id": "folder-1", "name": "Bugs"}]}),
            (200, {"lists": [{"id": "list-1", "name": "Site"}]}),
            (200, {"lists": [{"id": "list-2", "name": "Geral"}]}),
        ])
        service._session = session
        catalog = await service.get_list_catalog("workspace-1")
        self.assertEqual([item["id"] for item in catalog], ["list-1", "list-2"])
        self.assertEqual(catalog[0]["path"], "Meu Workspace > Produto > Bugs > Site")
        await service.close()

    async def test_parent_and_subtask_payloads(self):
        service = ClickUpService("secret-token")
        session = FakeSession([(200, {"id": "parent-1", "url": "https://clickup.test/parent-1"}), (200, {"id": "sub-1"})])
        service._session = session
        parent = await service.create_task("123", "Título", "Descrição", priority="urgent", custom_fields=[{"id": "risk", "value": "opt"}])
        subtask = await service.create_subtask("123", "parent-1", "Investigar endpoint", "Executar teste", priority="high")
        self.assertEqual(parent["id"], "parent-1")
        self.assertEqual(subtask["id"], "sub-1")
        self.assertEqual(session.requests[0][2]["json"]["priority"], 1)
        self.assertEqual(session.requests[0][2]["json"]["custom_fields"], [{"id": "risk", "value": "opt"}])
        self.assertEqual(session.requests[1][2]["json"], {"name": "Investigar endpoint", "markdown_content": "Executar teste", "priority": 2, "parent": "parent-1"})
        await service.close()

    async def test_subtask_failure_does_not_prevent_next_subtask(self):
        service = ClickUpService("secret-token")
        session = FakeSession([(400, {"err": "invalid field"}), (200, {"id": "sub-2"})])
        service._session = session
        with self.assertRaises(Exception):
            await service.create_subtask("123", "parent-1", "Primeira", "Falha esperada")
        second = await service.create_subtask("123", "parent-1", "Segunda", "Deve continuar")
        self.assertEqual(second["id"], "sub-2")
        self.assertEqual(len(session.requests), 2)
        await service.close()


if __name__ == "__main__":
    unittest.main()
