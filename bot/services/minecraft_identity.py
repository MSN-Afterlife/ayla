import hashlib
import hmac
import json
import secrets
import sqlite3
import string
import uuid
import unicodedata
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bot.config import Settings

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
CODE_TTL_SECONDS = 600
MAX_ACTIVE_CODES_PER_ACCOUNT = 3
MAX_ACTIVE_CODES_PER_IP = 10
UTC = timezone.utc
SUPPORTED_PLATFORMS = frozenset({"java", "bedrock"})
MAX_XUID = (1 << 64) - 1


class MinecraftLinkError(ValueError):
    pass


class MinecraftConflict(MinecraftLinkError):
    pass


def normalize_java_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value).strip())).lower()
    except (ValueError, AttributeError, TypeError):
        raise MinecraftLinkError("external_id deve ser um UUID Java valido.") from None


def normalize_platform(value: str) -> str:
    platform = str(value or "").strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise MinecraftLinkError("platform deve ser java ou bedrock.")
    return platform


def normalize_bedrock_xuid(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise MinecraftLinkError("external_id Bedrock deve ser um XUID decimal positivo.")
    number = int(raw)
    if number <= 0 or number > MAX_XUID:
        raise MinecraftLinkError("external_id Bedrock deve estar entre 1 e 2^64-1.")
    return str(number)


def normalize_external_id(platform: str, external_id: str) -> str:
    platform = normalize_platform(platform)
    return normalize_java_uuid(external_id) if platform == "java" else normalize_bedrock_xuid(external_id)


def sanitize_minecraft_username(platform: str, username: str) -> str:
    value = str(username or "").strip()
    if not value:
        raise MinecraftLinkError("username nao pode ser vazio.")
    if platform == "java" and (len(value) > 16 or any(c not in string.ascii_letters + string.digits + "_" for c in value)):
        raise MinecraftLinkError("username Java invalido.")
    if len(value) > 64:
        raise MinecraftLinkError("username/gamertag muito longo.")
    sanitized = "".join(c for c in value if not unicodedata.category(c).startswith("C"))[:64]
    if not sanitized:
        raise MinecraftLinkError("username nao pode ser vazio.")
    return sanitized


def normalize_minecraft_canonical_name(discord_name: str) -> str:
    """Minecraft-safe name; unsupported Discord characters are removed, not transliterated."""
    safe = "".join(c for c in str(discord_name or "").strip() if c in string.ascii_letters + string.digits + "_")
    return safe[:16] or "AylaUser"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


class MinecraftIdentityStore:
    def __init__(self, settings: Settings, *, now=None) -> None:
        self.database_path = Path(settings.levels_database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.internal_token = settings.ayla_minecraft_internal_token
        self._clock = now or _now
        self._apply_migrations()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _apply_migrations(self) -> None:
        migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
        with closing(self._connect()) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            for version in ("001_minecraft_identity", "002_minecraft_bedrock", "003_minecraft_migration_audit"):
                if connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone():
                    continue
                path = migrations_dir / f"{version}.sql"
                connection.executescript(path.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, _iso(self._clock())))

    def _audit(self, connection, event_type: str, discord_user_id=None, platform=None, external_id=None, metadata=None) -> None:
        connection.execute("INSERT INTO minecraft_audit_events(event_type, discord_user_id, platform, external_id, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)", (event_type, discord_user_id, platform, external_id, json.dumps(metadata or {}, ensure_ascii=False), _iso(self._clock())))

    def request_link_code(self, platform: str, external_id: str, username: str, requested_ip: str | None = None) -> dict[str, Any]:
        platform = normalize_platform(platform)
        external_id = normalize_external_id(platform, external_id)
        username = sanitize_minecraft_username(platform, username)
        now = self._clock()
        with closing(self._connect()) as connection:
            account = connection.execute("SELECT i.* FROM minecraft_accounts a JOIN minecraft_identities i ON i.id=a.identity_id WHERE a.platform=? AND a.external_id=?", (platform, external_id)).fetchone()
            if account:
                return {"linked": True, "enabled": bool(account["enabled"]), "identity": self._identity_payload(account)}
            cutoff = _iso(now)
            if connection.execute("SELECT COUNT(*) n FROM minecraft_link_codes WHERE platform=? AND external_id=? AND consumed_at IS NULL AND expires_at > ?", (platform, external_id, cutoff)).fetchone()["n"] >= MAX_ACTIVE_CODES_PER_ACCOUNT:
                raise MinecraftLinkError("Limite de codigos ativos atingido para esta conta. Aguarde a expiracao.")
            if requested_ip and connection.execute("SELECT COUNT(*) n FROM minecraft_link_codes WHERE requested_ip=? AND consumed_at IS NULL AND expires_at > ?", (requested_ip, cutoff)).fetchone()["n"] >= MAX_ACTIVE_CODES_PER_IP:
                raise MinecraftLinkError("Limite de codigos ativos atingido para este IP.")
            code = self._new_code(connection)
            connection.execute("INSERT INTO minecraft_link_codes(code, platform, external_id, username, created_at, expires_at, requested_ip) VALUES (?, ?, ?, ?, ?, ?, ?)", (code, platform, external_id, username, _iso(now), _iso(now + timedelta(seconds=CODE_TTL_SECONDS)), requested_ip))
            self._audit(connection, "link_code_created", platform=platform, external_id=external_id, metadata={"username": username})
        return {"linked": False, "code": code, "expires_in": CODE_TTL_SECONDS}

    def _new_code(self, connection) -> str:
        for _ in range(20):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if not connection.execute("SELECT 1 FROM minecraft_link_codes WHERE code=?", (code,)).fetchone():
                return code
        raise MinecraftLinkError("Nao foi possivel gerar um codigo agora.")

    def link_code(self, discord_user_id: str, discord_name: str, code: str) -> dict[str, Any]:
        code = str(code or "").strip().upper()
        if len(code) != CODE_LENGTH or any(c not in CODE_ALPHABET for c in code):
            raise MinecraftLinkError("Codigo invalido.")
        now = self._clock()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM minecraft_link_codes WHERE code=?", (code,)).fetchone()
            if not row:
                self._audit(connection, "invalid_link_attempt", discord_user_id=discord_user_id, metadata={"reason": "not_found"})
                connection.commit()
                raise MinecraftLinkError("Codigo inexistente.")
            if row["consumed_at"]:
                connection.commit()
                raise MinecraftLinkError("Codigo ja consumido.")
            if row["expires_at"] <= _iso(now):
                connection.commit()
                raise MinecraftLinkError("Codigo expirado. Gere um novo codigo no servidor.")
            owner = connection.execute("SELECT i.* FROM minecraft_accounts a JOIN minecraft_identities i ON i.id=a.identity_id WHERE a.platform=? AND a.external_id=?", (row["platform"], row["external_id"])).fetchone()
            if owner and owner["discord_user_id"] != str(discord_user_id):
                self._audit(connection, "link_conflict", discord_user_id=discord_user_id, platform=row["platform"], external_id=row["external_id"], metadata={"reason": "account_owned"})
                connection.commit()
                raise MinecraftConflict("Esta conta Minecraft ja esta vinculada a outro Discord.")
            identity = connection.execute("SELECT * FROM minecraft_identities WHERE discord_user_id=?", (str(discord_user_id),)).fetchone()
            if identity and connection.execute("SELECT 1 FROM minecraft_accounts WHERE identity_id=? AND platform=? AND external_id<>?", (identity["id"], row["platform"], row["external_id"])).fetchone():
                self._audit(connection, "link_conflict", discord_user_id=discord_user_id, platform=row["platform"], external_id=row["external_id"], metadata={"reason": "discord_has_java"})
                connection.commit()
                raise MinecraftConflict("Este Discord ja possui outra conta Java vinculada.")
            if not identity:
                name = self._unique_name(connection, normalize_minecraft_canonical_name(discord_name), str(discord_user_id))
                identity_id = connection.execute("INSERT INTO minecraft_identities(discord_user_id, canonical_uuid, canonical_name, discord_name_original, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id", (str(discord_user_id), str(uuid.uuid4()), name, str(discord_name or "")[:255], _iso(now), _iso(now))).fetchone()["id"]
                identity = connection.execute("SELECT * FROM minecraft_identities WHERE id=?", (identity_id,)).fetchone()
                self._audit(connection, "identity_created", discord_user_id=discord_user_id, platform=row["platform"], external_id=row["external_id"], metadata={"canonical_name": name})
            connection.execute("INSERT INTO minecraft_accounts(identity_id, platform, external_id, current_username, verified_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (identity["id"], row["platform"], row["external_id"], row["username"], _iso(now), _iso(now), _iso(now)))
            connection.execute("UPDATE minecraft_link_codes SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (_iso(now), row["id"]))
            self._audit(connection, "account_linked", discord_user_id=discord_user_id, platform=row["platform"], external_id=row["external_id"], metadata={"username": row["username"]})
            connection.commit()
        return {"identity": self._identity_payload(identity), "minecraft_account": {"platform": row["platform"], "external_id": row["external_id"], "current_username": row["username"]}}

    def _unique_name(self, connection, base: str, discord_user_id: str) -> str:
        if not connection.execute("SELECT 1 FROM minecraft_identities WHERE canonical_name=? COLLATE NOCASE", (base,)).fetchone():
            return base
        suffix = hashlib.sha256(discord_user_id.encode()).hexdigest()[:5]
        return (base[:16 - len(suffix) - 1] + "_" + suffix)[:16]

    def lookup_account(self, platform: str, external_id: str) -> dict[str, Any]:
        platform = normalize_platform(platform)
        external_id = normalize_external_id(platform, external_id)
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT i.*, a.platform, a.external_id, a.current_username FROM minecraft_accounts a JOIN minecraft_identities i ON i.id=a.identity_id WHERE a.platform=? AND a.external_id=?", (platform, external_id)).fetchone()
        if not row:
            return {"linked": False}
        return {"linked": True, "enabled": bool(row["enabled"]), "identity": self._identity_payload(row), "minecraft_account": {"platform": row["platform"], "external_id": row["external_id"], "current_username": row["current_username"]}}

    def lookup_java(self, external_id: str) -> dict[str, Any]:
        return self.lookup_account("java", external_id)

    @staticmethod
    def _identity_payload(row) -> dict[str, str]:
        return {"discord_user_id": row["discord_user_id"], "canonical_uuid": row["canonical_uuid"], "canonical_name": row["canonical_name"]}


def valid_internal_token(expected: str | None, supplied: str | None) -> bool:
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))
