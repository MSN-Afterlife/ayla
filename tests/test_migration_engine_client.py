import asyncio
import unittest

from bot.services.migration_engine_client import (
    LockState,
    MigrationEngineAuthError,
    MigrationEngineClient,
    MigrationEngineInvalidResponse,
    MigrationEngineNotConfigured,
    MigrationEngineUnavailable,
    MigrationInspectRequest,
    MigrationPlanRequest,
    MigrationState,
    PresenceState,
)


INSPECT_PAYLOAD = {
    "identity": {
        "discord_user_id": "123",
        "canonical_uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "canonical_name": "Mounk",
        "java": {"platform": "java", "external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "username": "Mounk"},
        "bedrock": {"platform": "bedrock", "external_id": "2533274791234567", "username": "MounkBedrock"},
        "source_uuids": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
    },
    "presence": "OFFLINE_CONFIRMED",
    "lock_state": "ABSENT",
    "detected_data": ["playerdata"],
    "warnings": [],
    "blockers": [],
}

PLAN_PAYLOAD = {
    "migration_id": "mig-1",
    "source_identities": ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
    "canonical_target": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
    "operations": [{"dataset": "playerdata", "action": "rewrite_uuid", "count": 1}],
    "affected_files": ["world/playerdata/source.dat"],
    "affected_datasets": ["playerdata"],
    "conflicts": [],
    "warnings": [],
    "blockers": [],
    "rollback_available": True,
    "presence": "OFFLINE_CONFIRMED",
    "lock_state": "ABSENT",
}

EXECUTE_PAYLOAD = {
    "migration_id": "mig-1",
    "result": "executed",
    "migrated_datasets": ["playerdata"],
    "verifications": ["canonical_exists"],
    "snapshot_ref": "snap-1",
    "rollback_available": True,
    "presence": "OFFLINE_CONFIRMED",
    "lock_state": "UNLOCKED",
}

ROLLBACK_PAYLOAD = {
    "migration_id": "mig-1",
    "result": "rolled_back",
    "restored_data": ["playerdata"],
    "verifications": ["source_restored"],
    "presence": "OFFLINE_CONFIRMED",
    "lock_state": "UNLOCKED",
}

STATUS_PAYLOAD = {
    "migration_id": "mig-1",
    "state": "PLANNED",
    "presence": "OFFLINE_CONFIRMED",
    "lock_state": "ABSENT",
    "verification_state": "ready",
    "rollback_available": True,
    "warnings": [],
    "blockers": [],
}


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeSession:
    calls = []
    responses = []
    raises = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def request(self, method, url, json=None, headers=None):
        if self.raises:
            raise self.raises
        self.calls.append((method, url, json, headers))
        status, payload = self.responses.pop(0)
        return FakeResponse(status, payload)


class MigrationEngineClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeSession.calls = []
        FakeSession.responses = []
        FakeSession.raises = None
        self.client = MigrationEngineClient("https://engine.test", "secret", session_factory=FakeSession)

    async def test_client_inspect_success(self):
        FakeSession.responses = [(200, INSPECT_PAYLOAD)]
        result = await self.client.inspect(MigrationInspectRequest(discord_user_id="123"))
        self.assertEqual(result.identity.discord_user_id, "123")
        self.assertEqual(result.presence, PresenceState.OFFLINE_CONFIRMED)
        self.assertEqual(FakeSession.calls[0][3]["Authorization"], "Bearer secret")

    async def test_client_plan_success(self):
        FakeSession.responses = [(200, PLAN_PAYLOAD)]
        result = await self.client.plan(MigrationPlanRequest(canonical_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff"))
        self.assertEqual(result.migration_id, "mig-1")
        self.assertTrue(result.allows_mutation())

    async def test_client_execute_success(self):
        FakeSession.responses = [(200, EXECUTE_PAYLOAD)]
        result = await self.client.execute("mig-1", operator_id="42", idempotency_key="idem-1")
        self.assertEqual(result.result, "executed")
        self.assertEqual(FakeSession.calls[0][3]["Idempotency-Key"], "idem-1")

    async def test_client_rollback_success(self):
        FakeSession.responses = [(200, ROLLBACK_PAYLOAD)]
        result = await self.client.rollback("mig-1", operator_id="42", idempotency_key="idem-2")
        self.assertEqual(result.restored_data, ("playerdata",))

    async def test_status_success(self):
        FakeSession.responses = [(200, STATUS_PAYLOAD)]
        result = await self.client.status("mig-1")
        self.assertEqual(result.state, MigrationState.PLANNED)

    async def test_timeout(self):
        FakeSession.raises = asyncio.TimeoutError()
        with self.assertRaises(MigrationEngineUnavailable):
            await self.client.status("mig-1")

    async def test_http_auth_failure(self):
        FakeSession.responses = [(401, {"code": "auth_failed", "message": "bad token"})]
        with self.assertRaises(MigrationEngineAuthError):
            await self.client.status("mig-1")

    async def test_malformed_response(self):
        FakeSession.responses = [(200, {"migration_id": "mig-1"})]
        with self.assertRaises(MigrationEngineInvalidResponse):
            await self.client.status("mig-1")

    async def test_missing_config_is_explicit(self):
        client = MigrationEngineClient(None, None, session_factory=FakeSession)
        with self.assertRaises(MigrationEngineNotConfigured):
            await client.status("mig-1")

    async def test_list_migrations_success(self):
        FakeSession.responses = [
            (
                200,
                {
                    "migrations": [
                        {
                            "migration_id": "mig_bf4cb82cf767d195",
                            "state": "EXECUTED",
                            "canonical_uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                            "player_name": "Mounk",
                            "updated_at": "2026-09-06T12:00:00Z",
                            "rollback_available": True,
                        }
                    ]
                },
            )
        ]
        result = await self.client.list_migrations(query="moun", state=MigrationState.EXECUTED, rollback_available=True)
        self.assertEqual(result[0].migration_id, "mig_bf4cb82cf767d195")
        self.assertIn("Mounk", result[0].label())
        self.assertIn("rollback_available=true", FakeSession.calls[0][1])

    async def test_search_players_success(self):
        FakeSession.responses = [
            (
                200,
                {
                    "players": [
                        {
                            "canonical_uuid": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                            "player_name": "Mounk",
                            "discord_user_id": "123",
                            "java_external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "confidence": 0.92,
                        }
                    ]
                },
            )
        ]
        result = await self.client.search_players("mou")
        self.assertEqual(result[0].player_name, "Mounk")
        self.assertIn("query=mou", FakeSession.calls[0][1])


if __name__ == "__main__":
    unittest.main()
