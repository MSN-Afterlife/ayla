import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from bot.config import Settings


POSITIVE_PATTERNS = [
    r"\bobrigad[ao]\b",
    r"\bvaleu\b",
    r"\bboa\b",
    r"\blinda\b",
    r"\bfofa\b",
    r"\bgostei\b",
    r"\bte amo\b",
    r"\bamo voce\b",
    r"\bperfeit[ao]\b",
]

NEGATIVE_PATTERNS = [
    r"\bbur(r)?a\b",
    r"\bidiota\b",
    r"\bchata\b",
    r"\bmerda\b",
    r"\bporra\b",
    r"\bcaralho\b",
    r"\bvsf\b",
    r"\bfoda-se\b",
    r"\bcala a boca\b",
    r"\blixo\b",
    r"\binutil\b",
]

GOSSIP_BAD_PATTERNS = [
    r"(?P<name>[A-Za-zÀ-ÿ0-9_*. -]{2,32}?)\s+(?:falou|tava falando|anda falando|disse)\s+(?:mal|merda|bosta)\s+(?:de voce|da ayla|sobre voce|sobre a ayla)(?:\s+pelas costas)?",
    r"(?P<name>[A-Za-zÀ-ÿ0-9_*. -]{2,32}?)\s+(?:te xingou|xingou voce|xingou a ayla)",
]

GOSSIP_GOOD_PATTERNS = [
    r"(?P<name>[A-Za-zÀ-ÿ0-9_*. -]{2,32}?)\s+(?:falou bem|elogiou|defendeu)\s+(?:voce|a ayla|de voce|da ayla)",
    r"(?P<name>[A-Za-zÀ-ÿ0-9_*. -]{2,32}?)\s+(?:gosta|curte)\s+(?:de voce|da ayla)",
]


@dataclass(frozen=True)
class AffectionProfile:
    guild_id: int | None
    user_id: int
    user_name: str
    affection: int
    respect: int
    trust: int
    suspicion: int
    chaos: int
    sass: int
    positive_hits: int
    negative_hits: int
    last_event: str | None
    favorite_color: str | None
    memories: tuple[str, ...] = ()

    @property
    def mood_label(self) -> str:
        if self.suspicion >= 45:
            return "desconfiada dessa pessoa"
        if self.affection >= 45:
            return "muito apegada a essa pessoa"
        if self.affection >= 18:
            return "simpatiza bastante com essa pessoa"
        if self.affection <= -45:
            return "bem irritada com essa pessoa"
        if self.affection <= -18:
            return "com o pe atras com essa pessoa"
        return "neutra com essa pessoa"


class AffectionService:
    def __init__(self, settings: Settings) -> None:
        self._database_path = Path(settings.levels_database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def observe_message(self, guild_id: int | None, user_id: int, user_name: str, content: str) -> AffectionProfile:
        positive = _count_matches(POSITIVE_PATTERNS, content)
        negative = _count_matches(NEGATIVE_PATTERNS, content)
        delta = min(positive * 6, 12) - min(negative * 7, 18)
        trust_delta = min(positive * 4, 8) - min(negative * 6, 14)
        suspicion_delta = min(negative * 8, 18)
        chaos_delta = 3 if _looks_playful(content) else 0
        sass_delta = min(negative * 5, 15)

        lowered = content.lower()
        if "azul" in lowered and any(word in lowered for word in ("cor", "favorita", "gosta", "combina")):
            favorite_color = "azul"
        else:
            favorite_color = None

        event = None
        if positive and negative:
            event = "misturou carinho e provocacao"
        elif positive:
            event = "foi gentil com a Ayla"
        elif negative:
            event = "xingou ou provocou a Ayla"

        now = int(time.time())
        with closing(self._connect()) as connection:
            self._ensure_profile(connection, guild_id, user_id, user_name)
            changed = self._apply_gossip(connection, guild_id, content, user_name, now)
            if delta or event or favorite_color:
                connection.execute(
                    """
                    UPDATE affection_profiles
                    SET user_name = ?,
                        affection = MAX(-100, MIN(100, affection + ?)),
                        respect = MAX(-100, MIN(100, respect + ?)),
                        trust = MAX(-100, MIN(100, trust + ?)),
                        suspicion = MAX(0, MIN(100, suspicion + ?)),
                        chaos = MAX(0, MIN(100, chaos + ?)),
                        sass = MAX(0, MIN(100, sass + ?)),
                        positive_hits = positive_hits + ?,
                        negative_hits = negative_hits + ?,
                        last_event = COALESCE(?, last_event),
                        favorite_color = COALESCE(?, favorite_color),
                        updated_at = ?
                    WHERE scope_id = ? AND user_id = ?
                    """,
                    (
                        user_name,
                        delta,
                        -min(negative * 4, 12) + min(positive * 2, 6),
                        trust_delta,
                        suspicion_delta,
                        chaos_delta,
                        sass_delta,
                        positive,
                        negative,
                        event,
                        favorite_color,
                        now,
                        _scope_id(guild_id),
                        user_id,
                    ),
                )
                changed = True
            if changed:
                connection.commit()

        return self.get_profile(guild_id, user_id, user_name)

    def get_profile(self, guild_id: int | None, user_id: int, user_name: str) -> AffectionProfile:
        with closing(self._connect()) as connection:
            self._ensure_profile(connection, guild_id, user_id, user_name)
            connection.commit()
            row = connection.execute(
                """
                SELECT guild_id, user_id, user_name, affection, respect, trust, suspicion, chaos, sass,
                       positive_hits, negative_hits, last_event, favorite_color
                FROM affection_profiles
                WHERE scope_id = ? AND user_id = ?
                """,
                (_scope_id(guild_id), user_id),
            ).fetchone()

            memories = self._get_memories(connection, guild_id, user_name)

        memories = tuple(memories)
        memory_weight = _memory_weight(memories)

        return AffectionProfile(
            guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
            user_id=int(row["user_id"]),
            user_name=row["user_name"],
            affection=_clamp(int(row["affection"]) + memory_weight),
            respect=int(row["respect"]),
            trust=_clamp(int(row["trust"]) + memory_weight),
            suspicion=_clamp(int(row["suspicion"]) + max(0, -memory_weight), 0, 100),
            chaos=int(row["chaos"]),
            sass=int(row["sass"]),
            positive_hits=int(row["positive_hits"]),
            negative_hits=int(row["negative_hits"]),
            last_event=row["last_event"],
            favorite_color=row["favorite_color"],
            memories=memories,
        )

    def set_profile(
        self,
        guild_id: int | None,
        user_id: int,
        user_name: str,
        affection: int,
        respect: int,
    ) -> AffectionProfile:
        now = int(time.time())
        with closing(self._connect()) as connection:
            self._ensure_profile(connection, guild_id, user_id, user_name)
            connection.execute(
                """
                UPDATE affection_profiles
                SET user_name = ?, affection = ?, respect = ?, last_event = ?, updated_at = ?
                WHERE scope_id = ? AND user_id = ?
                """,
                (
                    user_name,
                    _clamp(affection),
                    _clamp(respect),
                    "foi ajustado manualmente pela equipe",
                    now,
                    _scope_id(guild_id),
                    user_id,
                ),
            )
            connection.commit()

        return self.get_profile(guild_id, user_id, user_name)

    def reset_profile(self, guild_id: int | None, user_id: int, user_name: str) -> AffectionProfile:
        now = int(time.time())
        with closing(self._connect()) as connection:
            self._ensure_profile(connection, guild_id, user_id, user_name)
            connection.execute(
                """
                UPDATE affection_profiles
                SET user_name = ?,
                    affection = 0,
                    respect = 0,
                    trust = 0,
                    suspicion = 0,
                    chaos = 0,
                    sass = 0,
                    positive_hits = 0,
                    negative_hits = 0,
                    last_event = ?,
                    favorite_color = NULL,
                    updated_at = ?
                WHERE scope_id = ? AND user_id = ?
                """,
                (user_name, "memoria afetiva resetada", now, _scope_id(guild_id), user_id),
            )
            connection.commit()

        return self.get_profile(guild_id, user_id, user_name)

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS affection_profiles (
                    scope_id TEXT NOT NULL,
                    guild_id INTEGER,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    affection INTEGER NOT NULL DEFAULT 0,
                    respect INTEGER NOT NULL DEFAULT 0,
                    trust INTEGER NOT NULL DEFAULT 0,
                    suspicion INTEGER NOT NULL DEFAULT 0,
                    chaos INTEGER NOT NULL DEFAULT 0,
                    sass INTEGER NOT NULL DEFAULT 0,
                    positive_hits INTEGER NOT NULL DEFAULT 0,
                    negative_hits INTEGER NOT NULL DEFAULT 0,
                    last_event TEXT,
                    favorite_color TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (scope_id, user_id)
                )
                """
            )
            self._ensure_column(connection, "affection_profiles", "trust", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "affection_profiles", "suspicion", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "affection_profiles", "chaos", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "affection_profiles", "sass", "INTEGER NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS affection_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    guild_id INTEGER,
                    target_name TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT NOT NULL,
                    weight INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_profile(self, connection: sqlite3.Connection, guild_id: int | None, user_id: int, user_name: str) -> None:
        now = int(time.time())
        connection.execute(
            """
            INSERT OR IGNORE INTO affection_profiles (scope_id, guild_id, user_id, user_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_scope_id(guild_id), guild_id, user_id, user_name, now, now),
        )

    def _ensure_named_profile(self, connection: sqlite3.Connection, guild_id: int | None, target_name: str, now: int) -> None:
        pseudo_id = -abs(hash((_scope_id(guild_id), _normalize_name(target_name))) % 2_000_000_000)
        connection.execute(
            """
            INSERT OR IGNORE INTO affection_profiles (scope_id, guild_id, user_id, user_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (_scope_id(guild_id), guild_id, pseudo_id, target_name.strip(), now, now),
        )

    def _apply_gossip(self, connection: sqlite3.Connection, guild_id: int | None, content: str, source_name: str, now: int) -> bool:
        searchable = _normalize_gossip_text(content)
        for pattern in GOSSIP_BAD_PATTERNS:
            match = re.search(pattern, searchable, re.IGNORECASE)
            if match:
                target_name = _clean_name(match.group("name"))
                if target_name and target_name.lower() not in {"ayla", source_name.lower()}:
                    self._remember_gossip(connection, guild_id, target_name, source_name, "falou mal da Ayla pelas costas", -18, now)
                    return True
                return False

        for pattern in GOSSIP_GOOD_PATTERNS:
            match = re.search(pattern, searchable, re.IGNORECASE)
            if match:
                target_name = _clean_name(match.group("name"))
                if target_name and target_name.lower() != "ayla":
                    self._remember_gossip(connection, guild_id, target_name, source_name, "foi bem falado para a Ayla", 12, now)
                    return True
                return False
        return False

    def _remember_gossip(
        self,
        connection: sqlite3.Connection,
        guild_id: int | None,
        target_name: str,
        source_name: str,
        note: str,
        weight: int,
        now: int,
    ) -> None:
        self._ensure_named_profile(connection, guild_id, target_name, now)
        connection.execute(
            """
            UPDATE affection_profiles
            SET affection = MAX(-100, MIN(100, affection + ?)),
                trust = MAX(-100, MIN(100, trust + ?)),
                suspicion = MAX(0, MIN(100, suspicion + ?)),
                last_event = ?,
                updated_at = ?
            WHERE scope_id = ? AND lower(user_name) = lower(?)
            """,
            (weight, weight, 22 if weight < 0 else -8, note, now, _scope_id(guild_id), target_name),
        )
        connection.execute(
            """
            INSERT INTO affection_memories (scope_id, guild_id, target_name, source_name, kind, note, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_scope_id(guild_id), guild_id, target_name, source_name, "gossip", note, weight, now),
        )

    def _get_memories(self, connection: sqlite3.Connection, guild_id: int | None, user_name: str) -> list[str]:
        rows = connection.execute(
            """
            SELECT source_name, note, weight
            FROM affection_memories
            WHERE scope_id = ? AND lower(target_name) = lower(?)
            ORDER BY created_at DESC
            LIMIT 4
            """,
            (_scope_id(guild_id), user_name),
        ).fetchall()
        return [f"{row['source_name']} disse que {user_name} {row['note']} ({row['weight']:+d})" for row in rows]

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _scope_id(guild_id: int | None) -> str:
    return str(guild_id) if guild_id is not None else "dm"


def _count_matches(patterns: list[str], content: str) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, content, re.IGNORECASE))


def _looks_playful(content: str) -> bool:
    lowered = content.lower()
    return any(marker in lowered for marker in ("kk", "kkkk", "meme", "zoeira", "kkkkk", "kkkkkk"))


def _clean_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name).strip(" ,.:;!?")
    cleaned = re.sub(r"^(?:ayla\s+)?(?:o|a)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" ,.:;!?")


def _normalize_name(name: str) -> str:
    return _clean_name(name).lower()


def _normalize_gossip_text(content: str) -> str:
    normalized = content.replace("você", "voce").replace("Você", "Voce").replace("voc?", "voce").replace("Voc?", "Voce")
    normalized = re.sub(r"^\s*ayla[,\s]+", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"^\s*(?:o|a)\s+", "", normalized, flags=re.IGNORECASE)
    return normalized.strip()


def _memory_weight(memories: tuple[str, ...]) -> int:
    total = 0
    for memory in memories:
        match = re.search(r"\(([+-]\d+)\)$", memory)
        if match:
            total += int(match.group(1))
    return _clamp(total, -40, 30)


def _clamp(value: int, minimum: int = -100, maximum: int = 100) -> int:
    return max(minimum, min(maximum, value))
