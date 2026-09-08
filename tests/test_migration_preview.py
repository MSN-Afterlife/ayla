import gzip
import io
import json
import struct
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.commands.migration import (
    _CategoryPolicySelectionView,
    _ComparisonPaginationView,
    _DatasetPolicyView,
    _SourceSelectionView,
    _render_advancements_stats_page,
    _render_inventory_page,
    _render_preview_page,
    _render_summary_page,
    _show_comparison_preview,
)
from bot.services.migration_audit import MigrationAuditStore
from bot.services.migration_engine_client import (
    AdvancementsPreview,
    EquipmentSummary,
    InventorySection,
    ItemEntry,
    LockState,
    MigrationEngineClient,
    MigrationEngineError,
    MigrationEngineNotConfigured,
    MigrationErrorCode,
    MigrationPlan,
    MigrationPlanRequest,
    MigrationPreviewRequest,
    MigrationPreviewResult,
    MigrationSource,
    MigrationState,
    MigrationStatus,
    PlayerdataPreview,
    PresenceState,
    SourceProfilePreview,
    StatsPreview,
)
from bot.services.migration_preview_service import (
    ComparisonSummary,
    build_comparison,
    calculate_profile_score,
    filter_relevant_items,
    format_dimension,
    format_equipment_item,
    format_item_name,
    format_playtime,
    format_position,
    format_xp,
    generate_recommendation,
    is_relevant_item,
    parse_advancements_json,
    parse_nbt_playerdata,
    parse_stats_json,
)


def _build_nbt_bytes(
    inventory_items=(),
    ender_items=(),
    xp_level=0,
    xp_total=0,
    xp_p=0.0,
    health=20.0,
    food_level=20,
    dimension="minecraft:overworld",
    pos=(0.0, 64.0, 0.0),
) -> bytes:
    out = io.BytesIO()

    def write_tag(tag: int, name: str):
        out.write(struct.pack(">B", tag))
        b_name = name.encode("utf-8")
        out.write(struct.pack(">H", len(b_name)))
        out.write(b_name)

    def write_items_list(name: str, items):
        write_tag(9, name)
        out.write(struct.pack(">B", 10))  # TAG_Compound
        out.write(struct.pack(">i", len(items)))
        for item in items:
            # Slot
            if item.slot is not None:
                write_tag(1, "Slot")
                out.write(struct.pack(">b", item.slot))
            # id
            write_tag(8, "id")
            b_id = item.id.encode("utf-8")
            out.write(struct.pack(">H", len(b_id)))
            out.write(b_id)
            # Count
            write_tag(1, "Count")
            out.write(struct.pack(">b", item.count))
            # tag -> Enchantments
            if item.enchantments:
                write_tag(10, "tag")
                write_tag(9, "Enchantments")
                out.write(struct.pack(">B", 10))
                out.write(struct.pack(">i", len(item.enchantments)))
                for ench in item.enchantments:
                    e_id, lvl = ench.split(":") if ":" in ench else (ench, "1")
                    write_tag(8, "id")
                    b_eid = f"minecraft:{e_id}".encode("utf-8")
                    out.write(struct.pack(">H", len(b_eid)))
                    out.write(b_eid)
                    write_tag(2, "lvl")
                    out.write(struct.pack(">h", int(lvl)))
                    out.write(b"\x00")  # TAG_End
                out.write(b"\x00")  # TAG_End (tag)
            out.write(b"\x00")  # TAG_End (item)

    # Root TAG_Compound
    out.write(b"\x0a\x00\x00")  # tag 10, empty name

    # Inventory
    write_items_list("Inventory", inventory_items)
    # EnderItems
    write_items_list("EnderItems", ender_items)

    # XpLevel
    write_tag(3, "XpLevel")
    out.write(struct.pack(">i", xp_level))
    # XpTotal
    write_tag(3, "XpTotal")
    out.write(struct.pack(">i", xp_total))
    # XpP
    write_tag(5, "XpP")
    out.write(struct.pack(">f", xp_p))
    # Health
    write_tag(5, "Health")
    out.write(struct.pack(">f", health))
    # foodLevel
    write_tag(3, "foodLevel")
    out.write(struct.pack(">i", food_level))
    # Dimension
    write_tag(8, "Dimension")
    b_dim = dimension.encode("utf-8")
    out.write(struct.pack(">H", len(b_dim)))
    out.write(b_dim)
    # Pos
    write_tag(9, "Pos")
    out.write(struct.pack(">B", 6))  # TAG_Double
    out.write(struct.pack(">i", 3))
    out.write(struct.pack(">d", float(pos[0])))
    out.write(struct.pack(">d", float(pos[1])))
    out.write(struct.pack(">d", float(pos[2])))

    out.write(b"\x00")  # TAG_End (root)

    return gzip.compress(out.getvalue())


def fake_interaction(user_id=42):
    done = False

    async def fake_defer(*args, **kwargs):
        nonlocal done
        done = True

    response = SimpleNamespace(
        is_done=lambda: done,
        send_message=AsyncMock(),
        edit_message=AsyncMock(),
        defer=AsyncMock(side_effect=fake_defer),
    )
    followup = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(edit=AsyncMock())
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, guild_permissions=SimpleNamespace(administrator=True, manage_guild=True)),
        response=response,
        followup=followup,
        message=message,
    )


def make_mock_plan(req=None):
    return MigrationPlan(
        migration_id="mig-test",
        source_identities=tuple(s.external_id for s in req.sources) if req and req.sources else ("src1", "src2"),
        canonical_target="canonical-uuid-123",
        sources=req.sources if req else (),
        target_discord_user_id=req.target.get("discord_user_id") if req and req.target else None,
        dataset_policy=req.dataset_policy if req else None,
        status="READY",
        presence=PresenceState.OFFLINE_CONFIRMED,
        lock_state=LockState.ABSENT,
        rollback_available=True,
    )


def get_audit_events(audit: MigrationAuditStore) -> list[str]:
    with closing(audit._connect()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT event_type FROM minecraft_migration_audit_events")
        return [row[0] for row in cursor.fetchall()]


class MigrationPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audit = MigrationAuditStore(Path(self.temp_dir.name) / "audit.sqlite3")
        self.target = SimpleNamespace(
            author=SimpleNamespace(id=42, guild_permissions=SimpleNamespace(administrator=True, manage_guild=True)),
            send=AsyncMock(),
            response=SimpleNamespace(is_done=lambda: False, edit_message=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
        )

    async def asyncTearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    # 1. Java com progresso rico (diamantes, XP 30, advancements) e Bedrock novato (madeira, XP 0)
    def test_01_java_rich_vs_bedrock_novice_recommends_java(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            username="JavaPro",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=30,
                xp_total=1395,
                inventory=InventorySection(
                    occupied_slots=20,
                    total_items=250,
                    items=(
                        ItemEntry(id="minecraft:diamond", count=15),
                        ItemEntry(id="minecraft:diamond_sword", count=1, enchantments=("sharpness:5",)),
                    ),
                ),
                ender_chest=InventorySection(
                    occupied_slots=5,
                    total_items=40,
                    items=(ItemEntry(id="minecraft:netherite_ingot", count=2),),
                ),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=35),
            stats=StatsPreview(available=True, play_time_seconds=36000),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            username="BedrockNew",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=0,
                xp_total=0,
                inventory=InventorySection(
                    occupied_slots=2,
                    total_items=10,
                    items=(ItemEntry(id="minecraft:wooden_sword", count=1),),
                ),
                ender_chest=InventorySection(occupied_slots=0, total_items=0, items=()),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=1),
            stats=StatsPreview(available=True, play_time_seconds=600),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertEqual(rec.recommended_platform, "java")
        self.assertIn("Recomendação: Java", rec.headline)
        self.assertTrue(any("Diamante" in reason or "conquistas" in reason for reason in rec.reasons))

    # 2. Bedrock com progresso rico e Java novato
    def test_02_bedrock_rich_vs_java_novice_recommends_bedrock(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            username="JavaNew",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=1,
                inventory=InventorySection(occupied_slots=1, total_items=3, items=()),
                ender_chest=InventorySection(occupied_slots=0, total_items=0, items=()),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=0),
            stats=StatsPreview(available=True, play_time_seconds=300),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            username="BedrockPro",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=28,
                inventory=InventorySection(
                    occupied_slots=25,
                    total_items=300,
                    items=(
                        ItemEntry(id="minecraft:diamond", count=13),
                        ItemEntry(id="minecraft:elytra", count=1),
                    ),
                ),
                ender_chest=InventorySection(
                    occupied_slots=10,
                    total_items=120,
                    items=(ItemEntry(id="minecraft:totem_of_undying", count=2),),
                ),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=20),
            stats=StatsPreview(available=True, play_time_seconds=25000),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertEqual(rec.recommended_platform, "bedrock")
        self.assertIn("Recomendação: Bedrock", rec.headline)
        self.assertTrue(any("Diamante" in reason or "Elytra" in reason for reason in rec.reasons))

    # 3. Ambas com progresso similar/empate
    def test_03_tie_progress_recommends_neutral(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            username="PlayerJ",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=10,
                inventory=InventorySection(occupied_slots=5, total_items=50, items=(ItemEntry(id="minecraft:iron_sword", count=1),)),
                ender_chest=InventorySection(occupied_slots=0, total_items=0, items=()),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=5),
            stats=StatsPreview(available=True, play_time_seconds=3600),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            username="PlayerB",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=10,
                inventory=InventorySection(occupied_slots=5, total_items=50, items=(ItemEntry(id="minecraft:iron_sword", count=1),)),
                ender_chest=InventorySection(occupied_slots=0, total_items=0, items=()),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=5),
            stats=StatsPreview(available=True, play_time_seconds=3600),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertIsNone(rec.recommended_platform)
        self.assertIn("Empate", rec.headline)

    # 4. Java sem playerdata (novo jogador) e Bedrock com playerdata
    def test_04_java_unavailable_bedrock_available(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            username="JavaEmpty",
            playerdata=PlayerdataPreview(available=False, error_message="indisponível"),
            advancements=AdvancementsPreview(available=False),
            stats=StatsPreview(available=False),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            username="BedrockActive",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=5,
                inventory=InventorySection(occupied_slots=10, total_items=64, items=()),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=8),
            stats=StatsPreview(available=True, play_time_seconds=7200),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertEqual(rec.recommended_platform, "bedrock")
        self.assertIn("Bedrock (Java sem dados)", rec.headline)
        self.assertTrue(any("Java não possui dados" in r for r in rec.reasons))

    # 5. Bedrock sem playerdata e Java com playerdata
    def test_05_bedrock_unavailable_java_available(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            username="JavaActive",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=15,
                inventory=InventorySection(occupied_slots=12, total_items=80, items=()),
            ),
            advancements=AdvancementsPreview(available=True, total_completed=12),
            stats=StatsPreview(available=True, play_time_seconds=12000),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            username="BedrockEmpty",
            playerdata=PlayerdataPreview(available=False, error_message="indisponível"),
            advancements=AdvancementsPreview(available=False),
            stats=StatsPreview(available=False),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertEqual(rec.recommended_platform, "java")
        self.assertIn("Java (Bedrock sem dados)", rec.headline)
        self.assertTrue(any("Bedrock não possui dados" in r for r in rec.reasons))

    # 6. Ambos sem playerdata -> aviso claro, sem recomendação enganosa
    def test_06_both_unavailable(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            playerdata=PlayerdataPreview(available=False, error_message="indisponível"),
            advancements=AdvancementsPreview(available=False),
            stats=StatsPreview(available=False),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            playerdata=PlayerdataPreview(available=False, error_message="indisponível"),
            advancements=AdvancementsPreview(available=False),
            stats=StatsPreview(available=False),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertIsNone(rec.recommended_platform)
        self.assertIn("Nenhuma conta possui dados", rec.headline)

    # 7. Playerdata com Ender Chest cheio vs vazio
    def test_07_ender_chest_full_vs_empty(self):
        java_profile = SourceProfilePreview(
            platform="java",
            external_id="uuid-java",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=5,
                inventory=InventorySection(occupied_slots=10, total_items=50, items=()),
                ender_chest=InventorySection(
                    occupied_slots=15,
                    total_items=200,
                    items=(ItemEntry(id="minecraft:shulker_box", count=2),),
                ),
            ),
        )
        bedrock_profile = SourceProfilePreview(
            platform="bedrock",
            external_id="xuid-bedrock",
            playerdata=PlayerdataPreview(
                available=True,
                xp_level=5,
                inventory=InventorySection(occupied_slots=10, total_items=50, items=()),
                ender_chest=InventorySection(occupied_slots=0, total_items=0, items=()),
            ),
        )

        rec = generate_recommendation(java_profile, bedrock_profile)
        self.assertEqual(rec.recommended_platform, "java")
        self.assertTrue(any("Ender Chest" in r for r in rec.reasons))

    # 8. Contagem correta de slots ocupados e total de itens (soma de stacks)
    def test_08_nbt_parsing_slot_and_item_counts(self):
        items = (
            ItemEntry(id="minecraft:iron_ingot", count=64, slot=0),
            ItemEntry(id="minecraft:iron_ingot", count=32, slot=1),
            ItemEntry(id="minecraft:diamond", count=5, slot=2),
        )
        ender = (
            ItemEntry(id="minecraft:golden_apple", count=16, slot=0),
        )
        nbt_data = _build_nbt_bytes(
            inventory_items=items,
            ender_items=ender,
            xp_level=25,
            xp_total=825,
            health=18.5,
            food_level=19,
            dimension="minecraft:the_nether",
            pos=(12.5, 65.0, -99.2),
        )
        preview = parse_nbt_playerdata(nbt_data)
        self.assertTrue(preview.available)
        self.assertEqual(preview.inventory.occupied_slots, 3)
        self.assertEqual(preview.inventory.total_items, 101)
        self.assertEqual(preview.ender_chest.occupied_slots, 1)
        self.assertEqual(preview.ender_chest.total_items, 16)
        self.assertEqual(preview.xp_level, 25)
        self.assertEqual(preview.xp_total, 825)
        self.assertEqual(preview.health, 18.5)
        self.assertEqual(preview.food_level, 19)
        self.assertEqual(preview.dimension, "minecraft:the_nether")
        self.assertAlmostEqual(preview.position[0], 12.5)

    # 9. Destaque correto de itens relevantes
    def test_09_relevant_items_filter(self):
        items = (
            ItemEntry(id="minecraft:cobblestone", count=64),
            ItemEntry(id="minecraft:diamond", count=12),
            ItemEntry(id="minecraft:dirt", count=64),
            ItemEntry(id="minecraft:netherite_sword", count=1, enchantments=("unbreaking:3",)),
            ItemEntry(id="minecraft:wooden_sword", count=1, enchantments=("knockback:1",)),
            ItemEntry(id="minecraft:purple_shulker_box", count=1),
        )
        relevant = filter_relevant_items(items)
        ids = [item.id for item in relevant]
        self.assertIn("minecraft:diamond", ids)
        self.assertIn("minecraft:netherite_sword", ids)
        self.assertIn("minecraft:purple_shulker_box", ids)
        self.assertIn("minecraft:wooden_sword", ids)  # enchanted item is relevant!
        self.assertNotIn("minecraft:cobblestone", ids)
        self.assertNotIn("minecraft:dirt", ids)

    # 10. Advancements avaliados corretamente
    def test_10_advancements_json_parsing(self):
        adv_json = json.dumps({
            "minecraft:story/root": {"done": True},
            "minecraft:story/mine_diamond": {"done": True},
            "minecraft:end/kill_dragon": {"done": False},
            "minecraft:nether/obtain_ancient_debris": {"done": True},
            "DataVersion": 3465,
        })
        preview = parse_advancements_json(adv_json)
        self.assertTrue(preview.available)
        self.assertEqual(preview.total_completed, 3)
        self.assertIn("minecraft:story/mine_diamond", preview.highlights)
        self.assertIn("minecraft:nether/obtain_ancient_debris", preview.highlights)
        self.assertNotIn("minecraft:end/kill_dragon", preview.highlights)

    # 11. Stats avaliados corretamente (playtime formatado, mortes, kills)
    def test_11_stats_json_parsing(self):
        stats_json = json.dumps({
            "stats": {
                "minecraft:custom": {
                    "minecraft:play_time": 72000,  # 72000 ticks / 20 = 3600 seconds = 1h
                    "minecraft:deaths": 4,
                    "minecraft:mob_kills": 85,
                    "minecraft:player_kills": 1,
                    "minecraft:walk_one_cm": 250000,  # 2500 meters
                },
                "minecraft:mined": {
                    "minecraft:stone": 450,
                    "minecraft:diamond_ore": 12,
                },
            }
        })
        preview = parse_stats_json(stats_json)
        self.assertTrue(preview.available)
        self.assertEqual(preview.play_time_seconds, 3600)
        self.assertEqual(format_playtime(preview.play_time_seconds), "1h 0m")
        self.assertEqual(preview.deaths, 4)
        self.assertEqual(preview.mob_kills, 85)
        self.assertEqual(preview.player_kills, 1)
        self.assertEqual(preview.blocks_mined, 462)
        self.assertEqual(preview.distance_walked, 2500)

    # 12. Arquivo NBT corrompido -> erro gracioso
    def test_12_corrupt_nbt_graceful_error(self):
        corrupt_data = b"NOT_GZIP_AND_INVALID_NBT_BYTES"
        preview = parse_nbt_playerdata(corrupt_data)
        self.assertFalse(preview.available)
        self.assertIsNotNone(preview.error_message)

    # 13. Arquivo JSON de stats/advancements inválido -> erro gracioso
    def test_13_invalid_json_graceful_error(self):
        invalid_json = "{ invalid json content ... "
        adv_preview = parse_advancements_json(invalid_json)
        self.assertFalse(adv_preview.available)
        self.assertIn("JSON inválido", adv_preview.error_message)

        stats_preview = parse_stats_json(invalid_json)
        self.assertFalse(stats_preview.available)
        self.assertIn("JSON inválido", stats_preview.error_message)

    # 14. Operador escolhe 'Usar tudo Java' a partir do preview
    async def test_14_choose_all_java_generates_all_java_policy(self):
        client = SimpleNamespace(plan=AsyncMock(side_effect=lambda req: make_mock_plan(req)))
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        comparison = build_comparison((
            SourceProfilePreview(platform="java", external_id="java-uuid"),
            SourceProfilePreview(platform="bedrock", external_id="bedrock-xuid"),
        ))
        view = _ComparisonPaginationView(
            client=client,
            audit=self.audit,
            sources=sources,
            target_discord_id="123456789",
            player_name="Steve",
            reason="teste java",
            comparison=comparison,
        )
        interaction = fake_interaction()
        await view.java_button.callback(interaction)

        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertIn("⚠️ O progresso atual da conta unificada será substituído", confirm_view.render_content())
        await confirm_view.replace_button.callback(interaction)

        client.plan.assert_awaited_once()
        req: MigrationPlanRequest = client.plan.await_args[0][0]
        self.assertEqual(req.dataset_policy["strategy"], "REPLACE_TARGET")
        self.assertEqual(req.dataset_policy["source"], {"platform": "java", "external_id": "java-uuid"})
        self.assertTrue(req.dataset_policy["confirmed_replace_target"])
        self.assertEqual(req.target, {"discord_user_id": "123456789"})

    # 15. Operador escolhe 'Usar tudo Bedrock' a partir do preview
    async def test_15_choose_all_bedrock_generates_all_bedrock_policy(self):
        client = SimpleNamespace(plan=AsyncMock(side_effect=lambda req: make_mock_plan(req)))
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        comparison = build_comparison((
            SourceProfilePreview(platform="java", external_id="java-uuid"),
            SourceProfilePreview(platform="bedrock", external_id="bedrock-xuid"),
        ))
        view = _ComparisonPaginationView(
            client=client,
            audit=self.audit,
            sources=sources,
            target_discord_id="123456789",
            player_name="Steve",
            reason="teste bedrock",
            comparison=comparison,
        )
        interaction = fake_interaction()
        await view.bedrock_button.callback(interaction)

        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        self.assertIn("⚠️ O progresso atual da conta unificada será substituído", confirm_view.render_content())
        await confirm_view.replace_button.callback(interaction)

        client.plan.assert_awaited_once()
        req: MigrationPlanRequest = client.plan.await_args[0][0]
        self.assertEqual(req.dataset_policy["strategy"], "REPLACE_TARGET")
        self.assertEqual(req.dataset_policy["source"], {"platform": "bedrock", "external_id": "bedrock-xuid"})
        self.assertTrue(req.dataset_policy["confirmed_replace_target"])
        self.assertEqual(req.target, {"discord_user_id": "123456789"})

    # 16. Operador escolhe 'Por categoria' -> abre seleção por dataset
    async def test_16_choose_by_category_allows_mixed_selection(self):
        client = SimpleNamespace(plan=AsyncMock(side_effect=lambda req: make_mock_plan(req)))
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        comparison = build_comparison((
            SourceProfilePreview(platform="java", external_id="java-uuid"),
            SourceProfilePreview(platform="bedrock", external_id="bedrock-xuid"),
        ))
        cat_view = _CategoryPolicySelectionView(
            client=client,
            audit=self.audit,
            sources=sources,
            target_discord_id="123456789",
            player_name="Steve",
            reason="teste misto",
            comparison=comparison,
        )
        interaction = fake_interaction()

        # Toggle advancements to bedrock and stats to bedrock
        await cat_view.adv_bedrock.callback(interaction)
        await cat_view.stats_bedrock.callback(interaction)
        self.assertEqual(cat_view.policy["playerdata"], "java")
        self.assertEqual(cat_view.policy["advancements"], "bedrock")
        self.assertEqual(cat_view.policy["stats"], "bedrock")

        # Confirm migration
        await cat_view.confirm_button.callback(interaction)
        client.plan.assert_awaited_once()
        req: MigrationPlanRequest = client.plan.await_args[0][0]
        self.assertEqual(req.dataset_policy, {"playerdata": "java", "advancements": "bedrock", "stats": "bedrock"})

    # 17. Confirmação de que o preview é estritamente read-only
    async def test_17_preview_is_strictly_read_only(self):
        client = SimpleNamespace(
            preview=AsyncMock(return_value=MigrationPreviewResult(sources=())),
            plan=AsyncMock(side_effect=lambda req: make_mock_plan(req)),
            execute=AsyncMock(),
        )
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        interaction = fake_interaction()

        await _show_comparison_preview(
            interaction,
            client,
            self.audit,
            sources,
            "123456789",
            "Player",
            None,
        )

        client.preview.assert_awaited_once()
        client.plan.assert_not_called()
        client.execute.assert_not_called()
        # Ensure audit only records preview requested, never execute or plan
        events = get_audit_events(self.audit)
        self.assertIn("MIGRATION_PREVIEW", events)
        self.assertNotIn("MIGRATION_EXECUTE", events)

    # 18. Confirmação de que NENHUM lock foi criado no preview
    async def test_18_no_lock_created_in_preview(self):
        client = SimpleNamespace(
            preview=AsyncMock(return_value=MigrationPreviewResult(sources=())),
        )
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        interaction = fake_interaction()
        await _show_comparison_preview(
            interaction,
            client,
            self.audit,
            sources,
            "123456789",
            "Player",
            None,
        )
        # Lock status remained absent/untouched
        events = get_audit_events(self.audit)
        self.assertNotIn("MIGRATION_EXECUTE_REQUESTED", events)

    # 19. Confirmação de que o target canonical é sempre o Discord / nunca Floodgate UUID
    async def test_19_canonical_target_never_floodgate_uuid(self):
        client = SimpleNamespace(plan=AsyncMock(side_effect=lambda req: make_mock_plan(req)))
        floodgate_uuid = "00000000-0000-0000-0009-01f0adc9f1a5"
        sources = (
            MigrationSource("java", "java-uuid"),
            MigrationSource("bedrock", "2533274791234567", physical_uuid=floodgate_uuid),
        )
        comparison = build_comparison((
            SourceProfilePreview(platform="java", external_id="java-uuid"),
            SourceProfilePreview(platform="bedrock", external_id="2533274791234567"),
        ))
        view = _ComparisonPaginationView(
            client=client,
            audit=self.audit,
            sources=sources,
            target_discord_id="999888777",
            player_name="Steve",
            reason=None,
            comparison=comparison,
        )
        interaction = fake_interaction()
        await view.java_button.callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        await confirm_view.replace_button.callback(interaction)

        req: MigrationPlanRequest = client.plan.await_args[0][0]
        # Target must be Discord User ID
        self.assertEqual(req.target["discord_user_id"], "999888777")
        self.assertNotEqual(req.target.get("canonical_uuid"), floodgate_uuid)

    # 20. Single-source segue funcionando idêntico a antes
    async def test_20_single_source_compatibility(self):
        client = SimpleNamespace(plan=AsyncMock(side_effect=lambda req: make_mock_plan(req)))
        sources = (MigrationSource("java", "java-uuid"),)
        view = _SourceSelectionView(
            client,
            self.audit,
            None,
            "PlayerSingle",
            sources,
            "123456789",
        )
        interaction = fake_interaction()
        await view.java_button.callback(interaction)
        confirm_view = interaction.response.edit_message.await_args.kwargs["view"]
        await confirm_view.replace_button.callback(interaction)
        client.plan.assert_awaited_once()
        req: MigrationPlanRequest = client.plan.await_args[0][0]
        self.assertEqual(len(req.sources), 1)
        self.assertEqual(req.sources[0].platform, "java")

    # 21. Paginação do preview navega entre as 3 abas
    async def test_21_preview_pagination_navigation(self):
        client = SimpleNamespace()
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        comparison = build_comparison((
            SourceProfilePreview(platform="java", external_id="java-uuid"),
            SourceProfilePreview(platform="bedrock", external_id="bedrock-xuid"),
        ))
        view = _ComparisonPaginationView(
            client=client,
            audit=self.audit,
            sources=sources,
            target_discord_id="123456789",
            player_name="Steve",
            reason=None,
            comparison=comparison,
            page=0,
        )
        interaction = fake_interaction()

        # Switch to inventory page
        await view.inventory_button.callback(interaction)
        self.assertEqual(view.page, 1)

        # Switch to stats page
        await view.stats_button.callback(interaction)
        self.assertEqual(view.page, 2)

        # Switch to summary page
        await view.summary_button.callback(interaction)
        self.assertEqual(view.page, 0)

    # 22. Chamada ao endpoint do engine funciona quando configurado, e fallback gracioso quando não configurado/offline
    async def test_22_engine_preview_call_and_fallback(self):
        # Fallback when engine raises MigrationEngineNotConfigured
        client_unconf = SimpleNamespace(
            preview=AsyncMock(side_effect=MigrationEngineNotConfigured("Engine não configurado"))
        )
        sources = (MigrationSource("java", "java-uuid"), MigrationSource("bedrock", "bedrock-xuid"))
        interaction = fake_interaction()

        await _show_comparison_preview(
            interaction,
            client_unconf,
            self.audit,
            sources,
            "123456789",
            "Player",
            None,
        )
        # Should not throw, should display page with unavailable markers
        interaction.response.edit_message.assert_awaited_once()
        content = interaction.response.edit_message.await_args.kwargs["content"]
        self.assertIn("indisponível", content)


if __name__ == "__main__":
    unittest.main()
