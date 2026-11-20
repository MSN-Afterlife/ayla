"""Configurable moderation embed templates managed via the site admin API."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import discord
from discord.utils import utcnow


MODERATION_ACTIONS: tuple[str, ...] = ("ban", "unban", "kick", "mute", "unmute", "warn")
ALLOWED_THUMBNAIL_MODES: tuple[str, ...] = ("user_avatar", "guild_icon", "custom", "none")
ALLOWED_ASSET_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
ASSET_URL_PREFIX = "/api/moderation/embeds/assets/"
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")

TEMPLATE_VARIABLES: dict[str, str] = {
    "user": "Nome de exibicao do usuario alvo",
    "user_mention": "Mencao do usuario alvo (<@id>)",
    "user_tag": "Username/tag do usuario alvo",
    "user_id": "ID numerico do usuario alvo",
    "moderator": "Nome de exibicao do moderador",
    "moderator_mention": "Mencao do moderador (<@id>)",
    "reason": "Motivo informado na punicao",
    "duration": "Duracao formatada (ex: 10m, 2h)",
    "case_id": "ID do caso de moderacao",
    "guild": "Nome do servidor",
    "warns_count": "Quantidade de advertencias ativas do usuario",
}

DEFAULT_MODERATION_EMBEDS: dict[str, dict[str, Any]] = {
    "ban": {
        "enabled": True,
        "title": "Usuario banido",
        "description": "{user_mention} foi banido do servidor.\n**Motivo:** {reason}\n**Moderador:** {moderator_mention}",
        "color": "#E74C3C",
        "imageUrl": None,
        "thumbnailMode": "user_avatar",
        "thumbnailUrl": None,
        "footer": "Caso #{case_id} | {guild}",
        "dmUser": False,
    },
    "unban": {
        "enabled": True,
        "title": "Usuario desbanido",
        "description": "{user_mention} (`{user_id}`) teve o banimento removido.\n**Motivo:** {reason}\n**Moderador:** {moderator_mention}",
        "color": "#2ECC71",
        "imageUrl": None,
        "thumbnailMode": "user_avatar",
        "thumbnailUrl": None,
        "footer": "Caso #{case_id} | {guild}",
        "dmUser": False,
    },
    "kick": {
        "enabled": True,
        "title": "Usuario expulso",
        "description": "{user_mention} foi expulso do servidor.\n**Motivo:** {reason}\n**Moderador:** {moderator_mention}",
        "color": "#E67E22",
        "imageUrl": None,
        "thumbnailMode": "user_avatar",
        "thumbnailUrl": None,
        "footer": "Caso #{case_id} | {guild}",
        "dmUser": False,
    },
    "mute": {
        "enabled": True,
        "title": "Usuario silenciado",
        "description": "{user_mention} foi silenciado por **{duration}**.\n**Motivo:** {reason}\n**Moderador:** {moderator_mention}",
        "color": "#F39C12",
        "imageUrl": None,
        "thumbnailMode": "user_avatar",
        "thumbnailUrl": None,
        "footer": "Caso #{case_id} | {guild}",
        "dmUser": False,
    },
    "unmute": {
        "enabled": True,
        "title": "Silenciamento removido",
        "description": "{user_mention} pode falar novamente.\n**Motivo:** {reason}\n**Moderador:** {moderator_mention}",
        "color": "#2ECC71",
        "imageUrl": None,
        "thumbnailMode": "user_avatar",
        "thumbnailUrl": None,
        "footer": "Caso #{case_id} | {guild}",
        "dmUser": False,
    },
    "warn": {
        "enabled": True,
        "title": "Usuario advertido",
        "description": "{user_mention} recebeu uma advertencia (**{warns_count}** ativa(s)).\n**Motivo:** {reason}\n**Moderador:** {moderator_mention}",
        "color": "#F1C40F",
        "imageUrl": None,
        "thumbnailMode": "user_avatar",
        "thumbnailUrl": None,
        "footer": "Caso #{case_id} | {guild}",
        "dmUser": False,
    },
}


def parse_embed_color(value: Any, fallback: int = 0x5865F2) -> int:
    if isinstance(value, int) and 0 <= value <= 0xFFFFFF:
        return value
    if isinstance(value, str):
        cleaned = value.strip().lstrip("#")
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        if re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
            return int(cleaned, 16)
    return fallback


def render_template(template: str, context: dict[str, Any]) -> str:
    if not template:
        return ""
    return _PLACEHOLDER_RE.sub(
        lambda match: str(context[match.group(1)]) if match.group(1) in context else match.group(0),
        template,
    )


def validate_embed_config_payload(action: str, payload: Any, *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if action not in MODERATION_ACTIONS:
        raise ValueError(f"Acao de moderacao invalida: {action}")
    if not isinstance(payload, dict):
        raise ValueError("Payload deve ser um objeto JSON.")

    base = copy.deepcopy(existing or DEFAULT_MODERATION_EMBEDS[action])

    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            raise ValueError("'enabled' deve ser booleano.")
        base["enabled"] = payload["enabled"]

    if "dmUser" in payload:
        if not isinstance(payload["dmUser"], bool):
            raise ValueError("'dmUser' deve ser booleano.")
        base["dmUser"] = payload["dmUser"]

    for key, max_len in (("title", 256), ("description", 4000), ("footer", 2048)):
        if key in payload:
            raw = payload[key]
            if raw is None:
                base[key] = ""
            elif isinstance(raw, str):
                if len(raw) > max_len:
                    raise ValueError(f"'{key}' excede o limite de {max_len} caracteres.")
                base[key] = raw
            else:
                raise ValueError(f"'{key}' deve ser uma string.")

    if "color" in payload:
        raw_color = payload["color"]
        if isinstance(raw_color, int):
            if not (0 <= raw_color <= 0xFFFFFF):
                raise ValueError("'color' fora do intervalo RGB (0..0xFFFFFF).")
            base["color"] = f"#{raw_color:06X}"
        elif isinstance(raw_color, str):
            cleaned = raw_color.strip().lstrip("#")
            if cleaned.lower().startswith("0x"):
                cleaned = cleaned[2:]
            if not re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
                raise ValueError("'color' deve ser um hex valido (ex: #E74C3C).")
            base["color"] = f"#{cleaned.upper()}"
        else:
            raise ValueError("'color' deve ser hex string ou inteiro.")

    if "thumbnailMode" in payload:
        mode = str(payload["thumbnailMode"]).strip()
        if mode not in ALLOWED_THUMBNAIL_MODES:
            raise ValueError(f"'thumbnailMode' deve ser um de: {', '.join(ALLOWED_THUMBNAIL_MODES)}.")
        base["thumbnailMode"] = mode

    for url_key in ("imageUrl", "thumbnailUrl"):
        if url_key in payload:
            raw_url = payload[url_key]
            if raw_url is None or str(raw_url).strip() == "":
                base[url_key] = None
            elif isinstance(raw_url, str):
                cleaned_url = raw_url.strip()
                if not (
                    cleaned_url.startswith("https://")
                    or cleaned_url.startswith("http://")
                    or cleaned_url.startswith(ASSET_URL_PREFIX)
                ):
                    raise ValueError(f"'{url_key}' deve ser uma URL http(s) ou asset local.")
                if len(cleaned_url) > 2048:
                    raise ValueError(f"'{url_key}' excede 2048 caracteres.")
                base[url_key] = cleaned_url
            else:
                raise ValueError(f"'{url_key}' deve ser uma string ou null.")

    return base


class ModerationEmbedConfigStore:
    def __init__(self, config_path: str | Path, assets_dir: str | Path) -> None:
        self.config_path = Path(config_path)
        self.assets_dir = Path(assets_dir)

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {k: v for k, v in payload.items() if k in MODERATION_ACTIONS and isinstance(v, dict)}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save_raw(self, data: dict[str, dict[str, Any]]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.config_path)

    def get_all(self) -> dict[str, dict[str, Any]]:
        stored = self._load_raw()
        result: dict[str, dict[str, Any]] = {}
        for action in MODERATION_ACTIONS:
            merged = copy.deepcopy(DEFAULT_MODERATION_EMBEDS[action])
            if action in stored:
                merged.update(stored[action])
            result[action] = merged
        return result

    def get_action(self, action: str) -> dict[str, Any]:
        if action not in MODERATION_ACTIONS:
            raise ValueError(f"Acao de moderacao invalida: {action}")
        return self.get_all()[action]

    def update_action(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        current_all = self.get_all()
        updated = validate_embed_config_payload(action, payload, existing=current_all.get(action))
        current_all[action] = updated
        self._save_raw(current_all)
        return updated

    def reset_action(self, action: str) -> dict[str, Any]:
        if action not in MODERATION_ACTIONS:
            raise ValueError(f"Acao de moderacao invalida: {action}")
        current_all = self.get_all()
        current_all[action] = copy.deepcopy(DEFAULT_MODERATION_EMBEDS[action])
        self._save_raw(current_all)
        return current_all[action]

    def save_asset(self, action: str, filename: str, data: bytes) -> str:
        if action not in MODERATION_ACTIONS:
            raise ValueError(f"Acao de moderacao invalida: {action}")
        extension = Path(Path(filename).name).suffix.lower()
        if extension not in ALLOWED_ASSET_EXTENSIONS:
            raise ValueError("Formato de imagem/GIF nao permitido. Use PNG, JPG, GIF ou WEBP.")
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError("O arquivo deve ter entre 1 byte e 10 MB.")

        self.assets_dir.mkdir(parents=True, exist_ok=True)
        target = self.assets_dir / f"{action}{extension}"
        target.write_bytes(data)
        for old in self.assets_dir.glob(f"{action}.*"):
            if old != target:
                old.unlink(missing_ok=True)

        asset_url = f"{ASSET_URL_PREFIX}{target.name}"
        self.update_action(action, {"imageUrl": asset_url})
        return asset_url

    def resolve_asset_path(self, filename: str) -> Path | None:
        safe_name = Path(filename).name
        if safe_name != filename or not re.fullmatch(r"[a-zA-Z0-9_-]+\.(png|jpg|jpeg|gif|webp)", safe_name):
            return None
        path = self.assets_dir / safe_name
        return path if path.is_file() else None

    def schema(self) -> dict[str, Any]:
        return {
            "actions": list(MODERATION_ACTIONS),
            "thumbnailModes": list(ALLOWED_THUMBNAIL_MODES),
            "allowedImageExtensions": sorted(ALLOWED_ASSET_EXTENSIONS),
            "templateVariables": TEMPLATE_VARIABLES,
            "fields": {
                "enabled": "boolean",
                "title": "string (max 256)",
                "description": "string (max 4000)",
                "color": "hex string (#RRGGBB)",
                "imageUrl": "string (http/https URL ou /api/moderation/embeds/assets/<file>) | null",
                "thumbnailMode": "user_avatar | guild_icon | custom | none",
                "thumbnailUrl": "string (URL) | null",
                "footer": "string (max 2048)",
                "dmUser": "boolean",
            },
        }

    def build_embed(
        self,
        action: str,
        context: dict[str, Any],
        *,
        user_avatar_url: str | None = None,
        guild_icon_url: str | None = None,
    ) -> tuple[discord.Embed, discord.File | None]:
        config = self.get_action(action)
        title = render_template(str(config.get("title") or ""), context).strip()
        description = render_template(str(config.get("description") or ""), context).strip()
        footer = render_template(str(config.get("footer") or ""), context).strip()
        color = parse_embed_color(config.get("color"))

        embed = discord.Embed(
            title=title or None,
            description=description or None,
            color=color,
            timestamp=utcnow(),
        )
        if footer:
            embed.set_footer(text=footer[:2048])

        thumb_mode = config.get("thumbnailMode", "user_avatar")
        if thumb_mode == "user_avatar" and user_avatar_url:
            embed.set_thumbnail(url=user_avatar_url)
        elif thumb_mode == "guild_icon" and guild_icon_url:
            embed.set_thumbnail(url=guild_icon_url)
        elif thumb_mode == "custom" and config.get("thumbnailUrl"):
            embed.set_thumbnail(url=str(config["thumbnailUrl"]))

        attachment: discord.File | None = None
        image_url = config.get("imageUrl")
        if isinstance(image_url, str) and image_url.strip():
            cleaned = image_url.strip()
            if cleaned.startswith(ASSET_URL_PREFIX):
                asset_name = cleaned[len(ASSET_URL_PREFIX):]
                asset_path = self.resolve_asset_path(asset_name)
                if asset_path is not None:
                    attachment = discord.File(asset_path, filename=asset_path.name)
                    embed.set_image(url=f"attachment://{asset_path.name}")
            else:
                embed.set_image(url=cleaned)

        return embed, attachment
