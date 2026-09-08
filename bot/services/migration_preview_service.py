from __future__ import annotations

import gzip
import io
import json
import logging
import struct
from dataclasses import dataclass, field
from typing import Any

from bot.services.migration_engine_client import (
    AdvancementsPreview,
    EquipmentSummary,
    InventorySection,
    ItemEntry,
    PlayerdataPreview,
    SourceProfilePreview,
    StatsPreview,
)

logger = logging.getLogger(__name__)

RELEVANT_ITEM_IDS = frozenset(
    {
        "minecraft:diamond",
        "minecraft:diamond_block",
        "minecraft:diamond_sword",
        "minecraft:diamond_pickaxe",
        "minecraft:diamond_axe",
        "minecraft:diamond_shovel",
        "minecraft:diamond_hoe",
        "minecraft:diamond_helmet",
        "minecraft:diamond_chestplate",
        "minecraft:diamond_leggings",
        "minecraft:diamond_boots",
        "minecraft:netherite_ingot",
        "minecraft:netherite_scrap",
        "minecraft:netherite_block",
        "minecraft:ancient_debris",
        "minecraft:netherite_sword",
        "minecraft:netherite_pickaxe",
        "minecraft:netherite_axe",
        "minecraft:netherite_shovel",
        "minecraft:netherite_hoe",
        "minecraft:netherite_helmet",
        "minecraft:netherite_chestplate",
        "minecraft:netherite_leggings",
        "minecraft:netherite_boots",
        "minecraft:emerald",
        "minecraft:emerald_block",
        "minecraft:elytra",
        "minecraft:totem_of_undying",
        "minecraft:enchanted_golden_apple",
        "minecraft:golden_apple",
        "minecraft:beacon",
        "minecraft:shulker_box",
        "minecraft:white_shulker_box",
        "minecraft:orange_shulker_box",
        "minecraft:magenta_shulker_box",
        "minecraft:light_blue_shulker_box",
        "minecraft:yellow_shulker_box",
        "minecraft:lime_shulker_box",
        "minecraft:pink_shulker_box",
        "minecraft:gray_shulker_box",
        "minecraft:light_gray_shulker_box",
        "minecraft:cyan_shulker_box",
        "minecraft:purple_shulker_box",
        "minecraft:blue_shulker_box",
        "minecraft:brown_shulker_box",
        "minecraft:green_shulker_box",
        "minecraft:red_shulker_box",
        "minecraft:black_shulker_box",
    }
)

ITEM_PRETTY_NAMES: dict[str, str] = {
    "minecraft:diamond": "Diamante",
    "minecraft:diamond_block": "Bloco de Diamante",
    "minecraft:diamond_sword": "Espada de Diamante",
    "minecraft:diamond_pickaxe": "Picareta de Diamante",
    "minecraft:diamond_axe": "Machado de Diamante",
    "minecraft:diamond_shovel": "Pá de Diamante",
    "minecraft:diamond_hoe": "Enxada de Diamante",
    "minecraft:diamond_helmet": "Capacete de Diamante",
    "minecraft:diamond_chestplate": "Peitoral de Diamante",
    "minecraft:diamond_leggings": "Calça de Diamante",
    "minecraft:diamond_boots": "Botas de Diamante",
    "minecraft:netherite_ingot": "Barra de Netherita",
    "minecraft:netherite_scrap": "Pedaço de Netherita",
    "minecraft:netherite_block": "Bloco de Netherita",
    "minecraft:ancient_debris": "Detritos Ancestrais",
    "minecraft:netherite_sword": "Espada de Netherita",
    "minecraft:netherite_pickaxe": "Picareta de Netherita",
    "minecraft:netherite_axe": "Machado de Netherita",
    "minecraft:netherite_shovel": "Pá de Netherita",
    "minecraft:netherite_hoe": "Enxada de Netherita",
    "minecraft:netherite_helmet": "Capacete de Netherita",
    "minecraft:netherite_chestplate": "Peitoral de Netherita",
    "minecraft:netherite_leggings": "Calça de Netherita",
    "minecraft:netherite_boots": "Botas de Netherita",
    "minecraft:emerald": "Esmeralda",
    "minecraft:emerald_block": "Bloco de Esmeralda",
    "minecraft:elytra": "Élitros (Elytra)",
    "minecraft:totem_of_undying": "Totem da Imortalidade",
    "minecraft:enchanted_golden_apple": "Maçã Dourada Encantada",
    "minecraft:golden_apple": "Maçã Dourada",
    "minecraft:beacon": "Sinalizador (Beacon)",
    "minecraft:shulker_box": "Caixa de Shulker",
}

ADVANCEMENT_HIGHLIGHTS = frozenset(
    {
        "minecraft:story/mine_diamond",
        "minecraft:story/enchant_item",
        "minecraft:story/shiny_gear",
        "minecraft:story/cure_zombie_villager",
        "minecraft:nether/fast_travel",
        "minecraft:nether/find_bastion",
        "minecraft:nether/obtain_ancient_debris",
        "minecraft:nether/netherite_armor",
        "minecraft:nether/summon_wither",
        "minecraft:end/kill_dragon",
        "minecraft:end/elytra",
        "minecraft:adventure/totem_of_undying",
        "minecraft:adventure/hero_of_the_village",
        "minecraft:adventure/adventuring_time",
    }
)


@dataclass(frozen=True)
class PreviewRecommendation:
    recommended_platform: str | None
    headline: str
    reasons: tuple[str, ...]
    scores: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ComparisonSummary:
    profiles: tuple[SourceProfilePreview, ...]
    recommendation: PreviewRecommendation
    java_profile: SourceProfilePreview | None = None
    bedrock_profile: SourceProfilePreview | None = None


def is_relevant_item(item_id: str, enchantments: tuple[str, ...] = ()) -> bool:
    clean_id = str(item_id or "").strip().lower()
    if not clean_id.startswith("minecraft:"):
        clean_id = f"minecraft:{clean_id}"
    if clean_id in RELEVANT_ITEM_IDS:
        return True
    if clean_id.endswith("_shulker_box"):
        return True
    if enchantments:
        return True
    return False


def format_item_name(item_id: str) -> str:
    clean_id = str(item_id or "").strip().lower()
    if not clean_id.startswith("minecraft:"):
        clean_id = f"minecraft:{clean_id}"
    if clean_id in ITEM_PRETTY_NAMES:
        return ITEM_PRETTY_NAMES[clean_id]
    if clean_id.endswith("_shulker_box"):
        color = clean_id.removeprefix("minecraft:").removesuffix("_shulker_box").replace("_", " ").title()
        return f"Shulker Box ({color})"
    raw_name = clean_id.removeprefix("minecraft:").replace("_", " ").title()
    return raw_name


def filter_relevant_items(items: tuple[ItemEntry, ...]) -> tuple[ItemEntry, ...]:
    return tuple(item for item in items if is_relevant_item(item.id, item.enchantments))


def format_playtime(seconds: int | None) -> str:
    if seconds is None:
        return "indisponível"
    if seconds <= 0:
        return "0m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_dimension(dim: str | None) -> str:
    if not dim:
        return "indisponível"
    dim_str = str(dim).strip().lower()
    if dim_str in ("minecraft:overworld", "0", "overworld"):
        return "Overworld"
    if dim_str in ("minecraft:the_nether", "-1", "nether"):
        return "The Nether"
    if dim_str in ("minecraft:the_end", "1", "end"):
        return "The End"
    return dim_str.removeprefix("minecraft:").replace("_", " ").title()


def format_position(pos: tuple[float, float, float] | None) -> str:
    if not pos or len(pos) < 3:
        return "indisponível"
    return f"X: {pos[0]:.0f}, Y: {pos[1]:.0f}, Z: {pos[2]:.0f}"


def format_xp(xp_level: int | None, xp_total: int | None) -> str:
    if xp_level is None and xp_total is None:
        return "indisponível"
    level = xp_level if xp_level is not None else 0
    total = f" ({xp_total:,} XP)" if xp_total is not None else ""
    return f"Nível {level}{total}"


def format_equipment_item(item: ItemEntry | None) -> str:
    if not item or not item.id:
        return "vazio"
    name = format_item_name(item.id)
    if item.enchantments:
        ench_desc = ", ".join(ench.split(":")[-1].replace("_", " ").title() for ench in item.enchantments[:2])
        return f"{name} ✨ ({ench_desc})"
    return name


# ---------------------------------------------------------------------------
# NBT Parser (Gzip + Safe Tag Parser)
# ---------------------------------------------------------------------------

class NBTParseError(Exception):
    pass


def parse_nbt_playerdata(raw_bytes: bytes) -> PlayerdataPreview:
    if not raw_bytes:
        return PlayerdataPreview(available=False, error_message="indisponível (arquivo vazio ou ausente)")

    try:
        decompressed = gzip.decompress(raw_bytes)
    except Exception:
        decompressed = raw_bytes

    stream = io.BytesIO(decompressed)

    def read_exact(n: int) -> bytes:
        b = stream.read(n)
        if len(b) != n:
            raise NBTParseError("NBT truncado: fim inesperado dos dados")
        return b

    def read_short() -> int:
        return struct.unpack(">h", read_exact(2))[0]

    def read_ushort() -> int:
        return struct.unpack(">H", read_exact(2))[0]

    def read_int() -> int:
        return struct.unpack(">i", read_exact(4))[0]

    def read_long() -> int:
        return struct.unpack(">q", read_exact(8))[0]

    def read_float() -> float:
        return struct.unpack(">f", read_exact(4))[0]

    def read_double() -> float:
        return struct.unpack(">d", read_exact(8))[0]

    def read_string() -> str:
        length = read_ushort()
        return read_exact(length).decode("utf-8", errors="replace")

    def read_payload(tag_type: int, depth: int = 0) -> Any:
        if depth > 64:
            raise NBTParseError("NBT inválido: profundidade máxima de aninhamento excedida")
        if tag_type == 0:
            return None
        if tag_type == 1:
            return struct.unpack(">b", read_exact(1))[0]
        if tag_type == 2:
            return read_short()
        if tag_type == 3:
            return read_int()
        if tag_type == 4:
            return read_long()
        if tag_type == 5:
            return read_float()
        if tag_type == 6:
            return read_double()
        if tag_type == 7:
            length = read_int()
            if length < 0:
                raise NBTParseError("NBT inválido: tamanho de array negativo")
            return read_exact(length)
        if tag_type == 8:
            return read_string()
        if tag_type == 9:
            elem_type = struct.unpack(">B", read_exact(1))[0]
            count = read_int()
            if count < 0:
                raise NBTParseError("NBT inválido: tamanho de lista negativo")
            items = []
            for _ in range(count):
                items.append(read_payload(elem_type, depth + 1))
            return items
        if tag_type == 10:
            compound = {}
            while True:
                elem_tag = struct.unpack(">B", read_exact(1))[0]
                if elem_tag == 0:
                    break
                elem_name = read_string()
                elem_val = read_payload(elem_tag, depth + 1)
                compound[elem_name] = elem_val
            return compound
        if tag_type == 11:
            length = read_int()
            if length < 0:
                raise NBTParseError("NBT inválido: tamanho de array negativo")
            return [read_int() for _ in range(length)]
        if tag_type == 12:
            length = read_int()
            if length < 0:
                raise NBTParseError("NBT inválido: tamanho de array negativo")
            return [read_long() for _ in range(length)]
        raise NBTParseError(f"Tag NBT desconhecida: {tag_type}")

    try:
        root_tag = struct.unpack(">B", read_exact(1))[0]
        if root_tag != 10:
            return PlayerdataPreview(available=False, error_message="NBT inválido: tag raiz não é TAG_Compound")
        _ = read_string()
        root_compound = read_payload(10)
    except (NBTParseError, struct.error, OSError, UnicodeDecodeError) as err:
        logger.warning("Falha ao parsear NBT de playerdata: %s", err)
        return PlayerdataPreview(available=False, error_message=f"NBT corrompido ({err})")

    if not isinstance(root_compound, dict):
        return PlayerdataPreview(available=False, error_message="NBT inválido: conteúdo vazio")

    return _extract_playerdata_from_compound(root_compound)


def _extract_playerdata_from_compound(compound: dict[str, Any]) -> PlayerdataPreview:
    inventory_items = _extract_items_from_nbt_list(compound.get("Inventory"))
    ender_items = _extract_items_from_nbt_list(compound.get("EnderItems"))

    inv_occupied = len(inventory_items)
    inv_total = sum(item.count for item in inventory_items)
    ender_occupied = len(ender_items)
    ender_total = sum(item.count for item in ender_items)

    inventory_sec = InventorySection(
        occupied_slots=inv_occupied,
        total_items=inv_total,
        items=inventory_items,
        available=True,
    )
    ender_sec = InventorySection(
        occupied_slots=ender_occupied,
        total_items=ender_total,
        items=ender_items,
        available=True,
    )

    equipment = _extract_equipment_from_inventory(inventory_items)

    xp_level = compound.get("XpLevel") if isinstance(compound.get("XpLevel"), int) else None
    xp_total = compound.get("XpTotal") if isinstance(compound.get("XpTotal"), int) else None
    xp_progress = float(compound.get("XpP", 0.0)) if isinstance(compound.get("XpP"), (int, float)) else None

    health = float(compound.get("Health", 0.0)) if isinstance(compound.get("Health"), (int, float)) else None
    food_level = compound.get("foodLevel") if isinstance(compound.get("foodLevel"), int) else None

    dim_raw = compound.get("Dimension")
    dimension = str(dim_raw) if dim_raw is not None else None

    pos_raw = compound.get("Pos")
    position = None
    if isinstance(pos_raw, list) and len(pos_raw) >= 3:
        try:
            position = (float(pos_raw[0]), float(pos_raw[1]), float(pos_raw[2]))
        except (ValueError, TypeError):
            position = None

    return PlayerdataPreview(
        available=True,
        inventory=inventory_sec,
        ender_chest=ender_sec,
        equipment=equipment,
        xp_level=xp_level,
        xp_total=xp_total,
        xp_progress=xp_progress,
        health=health,
        food_level=food_level,
        dimension=dimension,
        position=position,
    )


def _extract_items_from_nbt_list(nbt_list: Any) -> tuple[ItemEntry, ...]:
    if not isinstance(nbt_list, list):
        return ()
    results = []
    for elem in nbt_list:
        if not isinstance(elem, dict):
            continue
        item_id = str(elem.get("id") or "")
        if not item_id:
            continue
        count = int(elem.get("Count", 1))
        slot = int(elem.get("Slot", 0)) if "Slot" in elem else None

        enchantments = []
        tag = elem.get("tag")
        if isinstance(tag, dict):
            ench_list = tag.get("Enchantments") or tag.get("StoredEnchantments") or []
            if isinstance(ench_list, list):
                for ench in ench_list:
                    if isinstance(ench, dict):
                        e_id = str(ench.get("id", "")).removeprefix("minecraft:")
                        lvl = ench.get("lvl", 1)
                        if e_id:
                            enchantments.append(f"{e_id}:{lvl}")

        results.append(ItemEntry(id=item_id, count=count, enchantments=tuple(enchantments), slot=slot))
    return tuple(results)


def _extract_equipment_from_inventory(items: tuple[ItemEntry, ...]) -> EquipmentSummary:
    slot_map: dict[int, ItemEntry] = {}
    for item in items:
        if item.slot is not None:
            slot_map[item.slot] = item

    head = slot_map.get(103)
    chest = slot_map.get(102)
    legs = slot_map.get(101)
    feet = slot_map.get(100)
    offhand = slot_map.get(-106)
    mainhand = slot_map.get(0)

    return EquipmentSummary(
        mainhand=mainhand,
        offhand=offhand,
        head=head,
        chest=chest,
        legs=legs,
        feet=feet,
    )


# ---------------------------------------------------------------------------
# Advancements & Stats JSON Parsers
# ---------------------------------------------------------------------------

def parse_advancements_json(data: dict[str, Any] | str | bytes) -> AdvancementsPreview:
    if not data:
        return AdvancementsPreview(available=False, error_message="indisponível (sem dados de conquistas)")

    if isinstance(data, (str, bytes)):
        try:
            parsed = json.loads(data)
        except Exception as err:
            return AdvancementsPreview(available=False, error_message=f"JSON inválido ({err})")
    elif isinstance(data, dict):
        parsed = data
    else:
        return AdvancementsPreview(available=False, error_message="formato de conquistas inválido")

    if not isinstance(parsed, dict):
        return AdvancementsPreview(available=False, error_message="formato de conquistas inválido")

    completed_count = 0
    highlights = []

    for adv_id, adv_val in parsed.items():
        if not isinstance(adv_val, dict):
            continue
        if adv_id == "DataVersion":
            continue
        is_done = bool(adv_val.get("done") is True)
        if is_done:
            completed_count += 1
            clean_id = adv_id.lower()
            if clean_id in ADVANCEMENT_HIGHLIGHTS or any(clean_id.endswith(h) for h in ADVANCEMENT_HIGHLIGHTS):
                highlights.append(adv_id)

    return AdvancementsPreview(
        available=True,
        total_completed=completed_count,
        highlights=tuple(highlights),
    )


def parse_stats_json(data: dict[str, Any] | str | bytes) -> StatsPreview:
    if not data:
        return StatsPreview(available=False, error_message="indisponível (sem dados de estatísticas)")

    if isinstance(data, (str, bytes)):
        try:
            parsed = json.loads(data)
        except Exception as err:
            return StatsPreview(available=False, error_message=f"JSON inválido ({err})")
    elif isinstance(data, dict):
        parsed = data
    else:
        return StatsPreview(available=False, error_message="formato de estatísticas inválido")

    if not isinstance(parsed, dict):
        return StatsPreview(available=False, error_message="formato de estatísticas inválido")

    stats_block = parsed.get("stats")
    if not isinstance(stats_block, dict):
        stats_block = parsed

    custom = stats_block.get("minecraft:custom") or {}
    if not isinstance(custom, dict):
        custom = {}

    play_time_ticks = custom.get("minecraft:play_time") or custom.get("minecraft:play_one_minute")
    play_time_seconds = int(play_time_ticks // 20) if isinstance(play_time_ticks, (int, float)) else None

    deaths = custom.get("minecraft:deaths") if isinstance(custom.get("minecraft:deaths"), int) else None
    mob_kills = custom.get("minecraft:mob_kills") if isinstance(custom.get("minecraft:mob_kills"), int) else None
    player_kills = custom.get("minecraft:player_kills") if isinstance(custom.get("minecraft:player_kills"), int) else None

    walk_cm = custom.get("minecraft:walk_one_cm") or 0
    sprint_cm = custom.get("minecraft:sprint_one_cm") or 0
    distance_walked = int((walk_cm + sprint_cm) // 100) if (walk_cm or sprint_cm) else None

    mined_block = stats_block.get("minecraft:mined") or {}
    blocks_mined = sum(count for count in mined_block.values() if isinstance(count, int)) if isinstance(mined_block, dict) else None

    return StatsPreview(
        available=True,
        play_time_seconds=play_time_seconds,
        deaths=deaths,
        mob_kills=mob_kills,
        player_kills=player_kills,
        blocks_mined=blocks_mined,
        distance_walked=distance_walked,
    )


# ---------------------------------------------------------------------------
# Recommendation Engine
# ---------------------------------------------------------------------------

def calculate_profile_score(profile: SourceProfilePreview | None) -> tuple[float, list[str]]:
    if not profile:
        return 0.0, []

    score = 0.0
    details: list[str] = []

    pdata = profile.playerdata
    if pdata.available:
        inv_items = pdata.inventory.items
        ender_items = pdata.ender_chest.items
        all_items = inv_items + ender_items

        counts: dict[str, int] = {}
        for item in all_items:
            clean_id = item.id.lower()
            if not clean_id.startswith("minecraft:"):
                clean_id = f"minecraft:{clean_id}"
            counts[clean_id] = counts.get(clean_id, 0) + item.count

        netherite_count = counts.get("minecraft:netherite_ingot", 0) + counts.get("minecraft:netherite_scrap", 0) + counts.get("minecraft:ancient_debris", 0)
        diamond_count = counts.get("minecraft:diamond", 0) + counts.get("minecraft:diamond_block", 0) * 9
        elytra_count = counts.get("minecraft:elytra", 0)
        totem_count = counts.get("minecraft:totem_of_undying", 0)
        shulker_count = sum(cnt for i_id, cnt in counts.items() if "shulker_box" in i_id)

        netherite_gear = sum(counts.get(f"minecraft:netherite_{part}", 0) for part in ("sword", "pickaxe", "axe", "shovel", "hoe", "helmet", "chestplate", "leggings", "boots"))
        diamond_gear = sum(counts.get(f"minecraft:diamond_{part}", 0) for part in ("sword", "pickaxe", "axe", "shovel", "hoe", "helmet", "chestplate", "leggings", "boots"))

        item_score = (
            (netherite_count * 15.0)
            + (netherite_gear * 20.0)
            + (elytra_count * 25.0)
            + (totem_count * 10.0)
            + (diamond_count * 4.0)
            + (diamond_gear * 8.0)
            + (shulker_count * 6.0)
        )
        score += item_score

        if netherite_count > 0 or netherite_gear > 0:
            details.append(f"{netherite_count}x Netherita / {netherite_gear}x peças de Netherita")
        if elytra_count > 0:
            details.append(f"{elytra_count}x Elytra")
        if diamond_count > 0 or diamond_gear > 0:
            details.append(f"{diamond_count}x Diamantes / {diamond_gear}x peças de Diamante")
        if totem_count > 0:
            details.append(f"{totem_count}x Totem")
        if shulker_count > 0:
            details.append(f"{shulker_count}x Shulker Box")

        if pdata.ender_chest.occupied_slots > 0:
            score += 15.0 + min(pdata.ender_chest.occupied_slots * 1.5, 30.0)
            details.append(f"Ender Chest com {pdata.ender_chest.occupied_slots} slots ocupados")

        score += min(pdata.inventory.occupied_slots * 0.5, 18.0)

        if pdata.xp_level:
            score += pdata.xp_level * 1.0
            if pdata.xp_level >= 30:
                score += 10.0
            details.append(f"Nível {pdata.xp_level} de XP")

    if profile.advancements.available and profile.advancements.total_completed:
        score += profile.advancements.total_completed * 2.0
        details.append(f"{profile.advancements.total_completed} conquistas concluídas")

    if profile.stats.available:
        if profile.stats.play_time_seconds:
            hours = profile.stats.play_time_seconds / 3600.0
            score += min(hours * 3.0, 50.0)
            details.append(f"{format_playtime(profile.stats.play_time_seconds)} de jogo")
        if profile.stats.mob_kills:
            score += min(profile.stats.mob_kills * 0.1, 15.0)

    return score, details


def generate_recommendation(
    java_profile: SourceProfilePreview | None,
    bedrock_profile: SourceProfilePreview | None,
) -> PreviewRecommendation:
    java_has_data = bool(java_profile and (java_profile.playerdata.available or java_profile.advancements.available or java_profile.stats.available))
    bedrock_has_data = bool(bedrock_profile and (bedrock_profile.playerdata.available or bedrock_profile.advancements.available or bedrock_profile.stats.available))

    if not java_has_data and not bedrock_has_data:
        return PreviewRecommendation(
            recommended_platform=None,
            headline="Recomendação: Nenhuma conta possui dados salvos",
            reasons=(
                "Ambas as contas constam como inexistentes ou sem dados no servidor.",
                "Não há progresso anterior para comparar.",
            ),
            scores={"java": 0.0, "bedrock": 0.0},
        )

    if not java_has_data and bedrock_has_data:
        b_score, b_details = calculate_profile_score(bedrock_profile)
        reasons = [
            "A conta Java não possui dados salvos no servidor (perfil novo).",
            "A conta Bedrock possui dados salvos e progresso registrado.",
        ]
        if b_details:
            reasons.append("Destaques Bedrock: " + "; ".join(b_details[:3]))
        return PreviewRecommendation(
            recommended_platform="bedrock",
            headline="Recomendação: Bedrock (Java sem dados)",
            reasons=tuple(reasons),
            scores={"java": 0.0, "bedrock": b_score},
        )

    if java_has_data and not bedrock_has_data:
        j_score, j_details = calculate_profile_score(java_profile)
        reasons = [
            "A conta Bedrock não possui dados salvos no servidor (perfil novo).",
            "A conta Java possui dados salvos e progresso registrado.",
        ]
        if j_details:
            reasons.append("Destaques Java: " + "; ".join(j_details[:3]))
        return PreviewRecommendation(
            recommended_platform="java",
            headline="Recomendação: Java (Bedrock sem dados)",
            reasons=tuple(reasons),
            scores={"java": j_score, "bedrock": 0.0},
        )

    j_score, j_details = calculate_profile_score(java_profile)
    b_score, b_details = calculate_profile_score(bedrock_profile)

    scores = {"java": round(j_score, 1), "bedrock": round(b_score, 1)}

    assert java_profile is not None
    assert bedrock_profile is not None

    reasons_list: list[str] = []

    def get_count(profile: SourceProfilePreview, item_name: str) -> int:
        total = 0
        for item in profile.playerdata.inventory.items + profile.playerdata.ender_chest.items:
            clean = item.id.lower().removeprefix("minecraft:")
            if clean == item_name:
                total += item.count
        return total

    j_diamonds = get_count(java_profile, "diamond")
    b_diamonds = get_count(bedrock_profile, "diamond")
    if j_diamonds != b_diamonds:
        if j_diamonds > b_diamonds:
            reasons_list.append(f"Java possui {j_diamonds}x Diamante contra {b_diamonds}x em Bedrock.")
        else:
            reasons_list.append(f"Bedrock possui {b_diamonds}x Diamante contra {j_diamonds}x em Java.")

    j_netherite = get_count(java_profile, "netherite_ingot") + get_count(java_profile, "netherite_scrap")
    b_netherite = get_count(bedrock_profile, "netherite_ingot") + get_count(bedrock_profile, "netherite_scrap")
    if j_netherite != b_netherite:
        if j_netherite > b_netherite:
            reasons_list.append(f"Java possui {j_netherite}x Netherita contra {b_netherite}x em Bedrock.")
        else:
            reasons_list.append(f"Bedrock possui {b_netherite}x Netherita contra {j_netherite}x em Java.")

    j_elytra = get_count(java_profile, "elytra")
    b_elytra = get_count(bedrock_profile, "elytra")
    if j_elytra != b_elytra:
        winner = "Java" if j_elytra > b_elytra else "Bedrock"
        reasons_list.append(f"{winner} possui Elytra ({max(j_elytra, b_elytra)}x).")

    j_ec = java_profile.playerdata.ender_chest.occupied_slots
    b_ec = bedrock_profile.playerdata.ender_chest.occupied_slots
    if j_ec != b_ec:
        if j_ec > b_ec:
            reasons_list.append(f"Ender Chest de Java mais utilizado ({j_ec} slots vs {b_ec} slots).")
        else:
            reasons_list.append(f"Ender Chest de Bedrock mais utilizado ({b_ec} slots vs {j_ec} slots).")

    j_xp = java_profile.playerdata.xp_level or 0
    b_xp = bedrock_profile.playerdata.xp_level or 0
    if abs(j_xp - b_xp) >= 3:
        if j_xp > b_xp:
            reasons_list.append(f"Java tem nível de XP superior ({j_xp} vs {b_xp}).")
        else:
            reasons_list.append(f"Bedrock tem nível de XP superior ({b_xp} vs {j_xp}).")

    j_adv = java_profile.advancements.total_completed or 0
    b_adv = bedrock_profile.advancements.total_completed or 0
    if j_adv != b_adv:
        if j_adv > b_adv:
            reasons_list.append(f"Java concluiu mais conquistas ({j_adv} vs {b_adv}).")
        else:
            reasons_list.append(f"Bedrock concluiu mais conquistas ({b_adv} vs {j_adv}).")

    j_time = java_profile.stats.play_time_seconds or 0
    b_time = bedrock_profile.stats.play_time_seconds or 0
    if abs(j_time - b_time) >= 600:
        if j_time > b_time:
            reasons_list.append(f"Java tem mais tempo de jogo ({format_playtime(j_time)} vs {format_playtime(b_time)}).")
        else:
            reasons_list.append(f"Bedrock tem mais tempo de jogo ({format_playtime(b_time)} vs {format_playtime(j_time)}).")

    score_diff = abs(j_score - b_score)
    if score_diff <= 5.0 or (j_score == 0 and b_score == 0):
        if not reasons_list:
            reasons_list.append("Ambas as contas possuem nível equivalente de itens e tempo de jogo.")
        reasons_list.append("Avalie individualmente o inventário de cada perfil antes de escolher.")
        return PreviewRecommendation(
            recommended_platform=None,
            headline="Recomendação: Empate / Progresso similar",
            reasons=tuple(reasons_list),
            scores=scores,
        )

    if j_score > b_score:
        if not reasons_list:
            reasons_list.append("Perfil Java possui pontuação geral superior de itens e progresso.")
        return PreviewRecommendation(
            recommended_platform="java",
            headline="Recomendação: Java",
            reasons=tuple(reasons_list),
            scores=scores,
        )
    else:
        if not reasons_list:
            reasons_list.append("Perfil Bedrock possui pontuação geral superior de itens e progresso.")
        return PreviewRecommendation(
            recommended_platform="bedrock",
            headline="Recomendação: Bedrock",
            reasons=tuple(reasons_list),
            scores=scores,
        )


def build_comparison(profiles: tuple[SourceProfilePreview, ...]) -> ComparisonSummary:
    java_prof = next((p for p in profiles if p.platform == "java"), None)
    bedrock_prof = next((p for p in profiles if p.platform == "bedrock"), None)
    rec = generate_recommendation(java_prof, bedrock_prof)
    return ComparisonSummary(
        profiles=profiles,
        recommendation=rec,
        java_profile=java_prof,
        bedrock_profile=bedrock_prof,
    )

