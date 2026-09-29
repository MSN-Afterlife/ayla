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
    MigrationPreviewRequest,
    MigrationPreviewResult,
    MigrationSource,
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

PREVIEW_PAYLOAD = {
    "sources": [
        {
            "platform": "java",
            "external_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "username": "Mounk",
            "playerdata": {
                "available": True,
                "inventory": {
                    "occupied_slots": 15,
                    "total_items": 120,
                    "items": [
                        {"id": "minecraft:diamond", "count": 13, "enchantments": []},
                        {"id": "minecraft:diamond_sword", "count": 1, "enchantments": ["sharpness:5"]},
                    ],
                },
                "ender_chest": {
                    "occupied_slots": 2,
                    "total_items": 10,
                    "items": [{"id": "minecraft:netherite_ingot", "count": 2}],
                },
                "equipment": {
                    "mainhand": {"id": "minecraft:diamond_sword", "count": 1, "enchantments": ["sharpness:5"]},
                    "offhand": {"id": "minecraft:shield", "count": 1},
                    "armor": {
                        "head": {"id": "minecraft:diamond_helmet", "count": 1},
                        "chest": {"id": "minecraft:diamond_chestplate", "count": 1},
                        "legs": {"id": "minecraft:diamond_leggings", "count": 1},
                        "feet": {"id": "minecraft:diamond_boots", "count": 1},
                    },
                },
                "xp_level": 30,
                "xp_total": 1395,
                "xp_progress": 0.5,
                "health": 20.0,
                "food_level": 20,
                "dimension": "minecraft:overworld",
                "position": [100.5, 64.0, -250.0],
            },
            "advancements": {
                "available": True,
                "total_completed": 45,
                "highlights": ["minecraft:story/mine_diamond"],
            },
            "stats": {
                "available": True,
                "play_time_seconds": 18450,
                "deaths": 2,
                "mob_kills": 120,
                "player_kills": 0,
                "blocks_mined": 4500,
                "distance_walked": 12000,
            },
        },
        {
            "platform": "bedrock",
            "external_id": "2533274791234567",
            "username": "MounkBedrock",
            "playerdata": {
                "available": True,
                "inventory": {
                    "occupied_slots": 3,
                    "total_items": 15,
                    "items": [{"id": "minecraft:wooden_sword", "count": 1}],
                },
                "ender_chest": {"occupied_slots": 0, "total_items": 0, "items": []},
                "equipment": {},
                "xp_level": 1,
                "xp_total": 10,
                "health": 20.0,
                "food_level": 20,
            },
            "advancements": {"available": True, "total_completed": 1, "highlights": []},
            "stats": {"available": True, "play_time_seconds": 600, "deaths": 1, "mob_kills": 2},
        },
    ],
    "warnings": [],
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

    async def test_execute_modes_and_quiescence_proof_payload(self):
        FakeSession.responses = [(200, EXECUTE_PAYLOAD)]
        await self.client.execute("mig-1", operator_id="42", idempotency_key="idem-hot", execution_mode="HOT")
        self.assertEqual(FakeSession.calls[0][2], {"operator_id": "42", "execution_mode": "HOT"})
        FakeSession.responses = [(200, EXECUTE_PAYLOAD)]
        proof = {"mode": "PAPER_OFFLINE_CONFIRMED", "valid": True}
        await self.client.execute("mig-1", operator_id="42", idempotency_key="idem-q", execution_mode="QUIESCENT", quiescence_proof=proof)
        self.assertEqual(FakeSession.calls[-1][2], {"operator_id": "42", "execution_mode": "QUIESCENT", "quiescence_proof": proof})

    async def test_maintenance_contract_payloads(self):
        proof = {"mode": "PAPER_OFFLINE_CONFIRMED", "valid": True}
        FakeSession.responses = [(200, {"status": "stopped", "quiescence_proof": proof})]
        stopped = await self.client.maintenance_paper_stop("mig-1", operator_id="42")
        self.assertEqual(stopped.quiescence_proof, proof)
        self.assertEqual(FakeSession.calls[0][2], {"migration_id": "mig-1", "operator_id": "42", "timeout_seconds": 60})
        FakeSession.responses = [(200, {"status": "started"})]
        await self.client.maintenance_paper_start("mig-1", operator_id="42")
        self.assertEqual(FakeSession.calls[-1][2], {"migration_id": "mig-1", "operator_id": "42", "timeout_seconds": 180})
        FakeSession.responses = [(200, {"healthy": True})]
        await self.client.maintenance_health()
        self.assertEqual(FakeSession.calls[-1][0], "GET")
        FakeSession.responses = [(200, {"status": "released"})]
        await self.client.maintenance_lock_release("mig-1")
        self.assertEqual(FakeSession.calls[-1][2], {"migration_id": "mig-1", "force": False})

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

    async def test_attached_legacy_source_is_preserved_on_identity(self):
        FakeSession.responses = [(200, {**INSPECT_PAYLOAD, "identity": {
            **INSPECT_PAYLOAD["identity"],
            "canonical_name": "MdA",
            "sources": [
                {"platform": "java", "external_id": "033da90b-ad57-3894-a162-52cd829b137e", "username": "Dafakn", "source_type": "legacy"},
                {"platform": "canonical", "external_id": INSPECT_PAYLOAD["identity"]["canonical_uuid"], "source_type": "canonical"},
            ],
        }})]
        result = await self.client.inspect(MigrationInspectRequest(canonical_uuid=INSPECT_PAYLOAD["identity"]["canonical_uuid"]))
        self.assertEqual(len(result.identity.sources), 1)
        self.assertEqual(result.identity.sources[0].semantic_type, "LEGACY_JAVA")
        self.assertEqual(result.identity.sources[0].username, "Dafakn")

    async def test_legacy_alias_search_collapses_to_canonical_identity(self):
        FakeSession.responses = [(200, {"players": [
            {"canonical_uuid": "047398a1-4e44-4efc-92af-a28e1fd59e86", "canonical_name": "MdA", "discord_user_id": "42", "sources": [{"platform": "java", "external_id": "033da90b-ad57-3894-a162-52cd829b137e", "username": "Dafakn", "source_type": "legacy"}]},
            {"canonical_uuid": "047398a1-4e44-4efc-92af-a28e1fd59e86", "canonical_name": "MdA", "discord_user_id": "42", "sources": [{"platform": "java", "external_id": "linked-java", "username": "MdA", "source_type": "linked"}]},
        ]})]
        result = await self.client.search_players("Dafakn")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].player_name, "MdA")
        self.assertEqual({source.username for source in result[0].source_list()}, {"Dafakn", "MdA"})
        self.assertIn("Dafakn", result[0].label())

    async def test_staging_auth_bypass_status_success(self):
        FakeSession.responses = [(200, {"enabled": True})]
        result = await self.client.staging_auth_bypass_status()
        self.assertTrue(result)
        self.assertEqual(FakeSession.calls[0][0], "GET")
        self.assertTrue(FakeSession.calls[0][1].endswith("/api/v1/staging/auth-bypass"))

    async def test_set_staging_auth_bypass_success(self):
        FakeSession.responses = [(200, {"enabled": False})]
        result = await self.client.set_staging_auth_bypass(False)
        self.assertFalse(result)
        self.assertEqual(FakeSession.calls[0][0], "POST")
        self.assertEqual(FakeSession.calls[0][2], {"enabled": False})

    async def test_multi_source_plan_payload_uses_discord_target_and_policy(self):
        FakeSession.responses = [(200, {**PLAN_PAYLOAD, "status": "READY", "sources": [
            {"platform": "java", "external_id": "java-id", "username": "Mounkass"},
            {"platform": "bedrock", "external_id": "2535408009867685", "username": ".Mounkass"},
        ], "target": {"discord_user_id": "884592686101852232"}, "dataset_policy": {"playerdata": "java", "advancements": "java", "stats": "java"}})]
        request = MigrationPlanRequest(
            sources=(MigrationSource("java", "java-id", "Mounkass"), MigrationSource("bedrock", "2535408009867685", ".Mounkass")),
            target={"discord_user_id": "884592686101852232"},
            dataset_policy={"playerdata": "java", "advancements": "java", "stats": "java"},
            reason="multi-source test",
        )
        result = await self.client.plan(request)
        body = FakeSession.calls[0][2]
        self.assertEqual(body["target"], {"discord_user_id": "884592686101852232"})
        self.assertEqual([item["platform"] for item in body["sources"]], ["java", "bedrock"])
        self.assertEqual(result.target_discord_user_id, "884592686101852232")
        self.assertEqual(result.dataset_policy["playerdata"], "java")

    async def test_player_search_parses_independent_platform_candidates(self):
        FakeSession.responses = [(200, {"players": [
            {"player_name": "Mounkass", "platform": "java", "external_id": "java-id", "discord_user_id": "884592686101852232"},
            {"player_name": ".Mounkass", "platform": "bedrock", "external_id": "2535408009867685", "discord_user_id": "884592686101852232"},
        ]})]
        result = await self.client.search_players("Mounkass")
        self.assertEqual([item.source_list()[0].platform for item in result], ["java", "bedrock"])
        self.assertNotEqual(result[1].external_id, "00000000-0000-0000-0009-01f0adc9f1a5")

    async def test_preview_success(self):
        FakeSession.responses = [(200, PREVIEW_PAYLOAD)]
        request = MigrationPreviewRequest(
            sources=(
                MigrationSource("java", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "Mounk"),
                MigrationSource("bedrock", "2533274791234567", "MounkBedrock"),
            ),
            target={"discord_user_id": "123"},
        )
        result = await self.client.preview(request)
        self.assertEqual(FakeSession.calls[0][0], "POST")
        self.assertTrue(FakeSession.calls[0][1].endswith("/api/v1/migrations/preview"))
        self.assertEqual(len(result.sources), 2)
        java_src = result.sources[0]
        self.assertEqual(java_src.platform, "java")
        self.assertTrue(java_src.playerdata.available)
        self.assertEqual(java_src.playerdata.inventory.occupied_slots, 15)
        self.assertEqual(java_src.playerdata.inventory.total_items, 120)
        self.assertEqual(len(java_src.playerdata.inventory.items), 2)
        self.assertEqual(java_src.playerdata.xp_level, 30)
        self.assertEqual(java_src.advancements.total_completed, 45)
        self.assertEqual(java_src.stats.play_time_seconds, 18450)
        self.assertEqual(java_src.stats.deaths, 2)

    async def test_preview_partial_data_and_missing_fields(self):
        partial_payload = {
            "sources": [
                {
                    "platform": "java",
                    "external_id": "java-uuid",
                    "playerdata": {"available": False, "error_message": "playerdata não encontrado"},
                    "advancements": {"available": False},
                    "stats": {"available": False},
                }
            ],
            "warnings": ["Java playerdata indisponivel"],
        }
        FakeSession.responses = [(200, partial_payload)]
        request = MigrationPreviewRequest(
            sources=(MigrationSource("java", "java-uuid"),),
        )
        result = await self.client.preview(request)
        self.assertEqual(len(result.sources), 1)
        src = result.sources[0]
        self.assertFalse(src.playerdata.available)
        self.assertEqual(src.playerdata.error_message, "playerdata não encontrado")
        self.assertIsNone(src.playerdata.xp_level)
        self.assertEqual(result.warnings, ("Java playerdata indisponivel",))

    async def test_preview_not_configured(self):
        unconfigured = MigrationEngineClient(base_url=None, token=None)
        with self.assertRaises(MigrationEngineNotConfigured):
            await unconfigured.preview(MigrationPreviewRequest())

    async def test_plan_structural_operations_without_dataset_success(self):
        payload = {
            "migration_id": "mig-test-123",
            "canonical_target": "target-uuid",
            "source_identities": ["src-java", "src-bedrock"],
            "operations": [
                {
                    "action": "CREATE_CANONICAL_IDENTITY",
                    "detail": "Create canonical identity",
                    "canonical_uuid": "target-uuid",
                },
                {
                    "action": "ATTACH_JAVA_IDENTITY",
                    "platform": "java",
                    "external_id": "java-uuid",
                    "detail": "Attach java account",
                },
                {
                    "action": "ATTACH_BEDROCK_IDENTITY",
                    "platform": "bedrock",
                    "external_id": "12345678",
                    "detail": "Attach bedrock account",
                },
                {
                    "dataset": "playerdata",
                    "action": "MOVE_FILE",
                    "source": "world/playerdata/old.dat",
                    "target": "world/playerdata/new.dat",
                },
            ],
            "affected_datasets": ["playerdata"],
            "presence": "OFFLINE_CONFIRMED",
            "lock_state": "UNLOCKED",
            "rollback_available": True,
        }
        FakeSession.responses = [(200, payload)]
        request = MigrationPlanRequest(canonical_uuid="target-uuid")
        plan = await self.client.plan(request)
        self.assertEqual(len(plan.operations), 4)
        self.assertIsNone(plan.operations[0].dataset)
        self.assertEqual(plan.operations[0].action, "CREATE_CANONICAL_IDENTITY")
        self.assertIsNone(plan.operations[1].dataset)
        self.assertEqual(plan.operations[1].action, "ATTACH_JAVA_IDENTITY")
        self.assertIsNone(plan.operations[2].dataset)
        self.assertEqual(plan.operations[2].action, "ATTACH_BEDROCK_IDENTITY")
        self.assertEqual(plan.operations[3].dataset, "playerdata")
        self.assertEqual(plan.operations[3].action, "MOVE_FILE")

    async def test_plan_dataset_operation_without_dataset_raises_invalid_response(self):
        payload = {
            "migration_id": "mig-test-err",
            "canonical_target": "target-uuid",
            "source_identities": ["src-java"],
            "operations": [
                {
                    "action": "MOVE_FILE",
                    "source": "world/playerdata/old.dat",
                    "target": "world/playerdata/new.dat",
                },
            ],
            "presence": "OFFLINE_CONFIRMED",
            "lock_state": "UNLOCKED",
        }
        FakeSession.responses = [(200, payload)]
        request = MigrationPlanRequest(canonical_uuid="target-uuid")
        with self.assertRaises(MigrationEngineInvalidResponse) as ctx:
            await self.client.plan(request)
        self.assertIn("dataset", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
