import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

import discord


VALID_STATUSES = {"online", "idle", "dnd", "invisible"}
VALID_ACTIVITY_TYPES = {"playing", "jogando", "listening", "ouvindo", "watching", "assistindo", "competing", "competindo", "custom", "personalizado", "none", "nenhum"}


@dataclass(frozen=True)
class PresenceEntry:
    status: str = "online"
    activity_type: str = "custom"
    text: str = "faz sol hoje | a!help"
    emoji: str | None = None


@dataclass(frozen=True)
class PresenceConfig:
    mode: str = "single"
    interval_seconds: int = 300
    active_index: int = 0
    entries: tuple[PresenceEntry, ...] = (PresenceEntry(),)


class PresenceConfigStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def get(self) -> PresenceConfig:
        if not self._path.exists():
            config = PresenceConfig()
            self.save(config)
            return config

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return PresenceConfig()

        entries = tuple(
            PresenceEntry(
                status=_normalize_status(item.get("status", "online")),
                activity_type=_normalize_activity_type(item.get("activity_type", "custom")),
                text=str(item.get("text", "")).strip(),
                emoji=item.get("emoji"),
            )
            for item in raw.get("entries", [])
            if isinstance(item, dict)
        )
        if not entries:
            entries = (PresenceEntry(),)

        mode = str(raw.get("mode", "single")).lower()
        if mode not in {"single", "rotate"}:
            mode = "single"

        interval_seconds = _clamp_int(raw.get("interval_seconds", 300), 15, 86400)
        active_index = _clamp_int(raw.get("active_index", 0), 0, max(len(entries) - 1, 0))
        return PresenceConfig(mode=mode, interval_seconds=interval_seconds, active_index=active_index, entries=entries)

    def save(self, config: PresenceConfig) -> None:
        payload = {
            "mode": config.mode,
            "interval_seconds": config.interval_seconds,
            "active_index": config.active_index,
            "entries": [asdict(entry) for entry in config.entries],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def set_mode(self, mode: str) -> PresenceConfig:
        mode = mode.lower()
        if mode not in {"single", "rotate"}:
            raise ValueError("Modo invalido. Use `single` ou `rotate`.")
        config = self.get()
        updated = PresenceConfig(mode=mode, interval_seconds=config.interval_seconds, active_index=config.active_index, entries=config.entries)
        self.save(updated)
        return updated

    def set_interval(self, seconds: int) -> PresenceConfig:
        if seconds < 15:
            raise ValueError("O intervalo precisa ser de pelo menos 15 segundos.")
        config = self.get()
        updated = PresenceConfig(mode=config.mode, interval_seconds=seconds, active_index=config.active_index, entries=config.entries)
        self.save(updated)
        return updated

    def set_active(self, index: int) -> PresenceConfig:
        config = self.get()
        if index < 1 or index > len(config.entries):
            raise ValueError("Indice invalido.")
        updated = PresenceConfig(mode=config.mode, interval_seconds=config.interval_seconds, active_index=index - 1, entries=config.entries)
        self.save(updated)
        return updated

    def add_entry(self, status: str, activity_type: str, text: str, emoji: str | None = None) -> PresenceConfig:
        entry = PresenceEntry(status=_normalize_status(status), activity_type=_normalize_activity_type(activity_type), text=text.strip(), emoji=emoji)
        if entry.activity_type != "none" and not entry.text:
            raise ValueError("Informe o texto da atividade/status.")
        config = self.get()
        updated = PresenceConfig(
            mode=config.mode,
            interval_seconds=config.interval_seconds,
            active_index=config.active_index,
            entries=(*config.entries, entry),
        )
        self.save(updated)
        return updated

    def update_entry(self, index: int, status: str, activity_type: str, text: str, emoji: str | None = None) -> PresenceConfig:
        config = self.get()
        if index < 1 or index > len(config.entries):
            raise ValueError("Indice invalido.")
        entry = PresenceEntry(status=_normalize_status(status), activity_type=_normalize_activity_type(activity_type), text=text.strip(), emoji=emoji)
        entries = list(config.entries)
        entries[index - 1] = entry
        updated = PresenceConfig(mode=config.mode, interval_seconds=config.interval_seconds, active_index=min(config.active_index, len(entries) - 1), entries=tuple(entries))
        self.save(updated)
        return updated

    def remove_entry(self, index: int) -> PresenceConfig:
        config = self.get()
        if index < 1 or index > len(config.entries):
            raise ValueError("Indice invalido.")
        entries = list(config.entries)
        entries.pop(index - 1)
        if not entries:
            entries = [PresenceEntry(activity_type="none", text="", emoji=None)]
        active_index = min(config.active_index, len(entries) - 1)
        updated = PresenceConfig(mode=config.mode, interval_seconds=config.interval_seconds, active_index=active_index, entries=tuple(entries))
        self.save(updated)
        return updated

    def move_entry(self, source: int, target: int) -> PresenceConfig:
        config = self.get()
        if source < 1 or source > len(config.entries) or target < 1 or target > len(config.entries):
            raise ValueError("Indice invalido.")
        entries = list(config.entries)
        entry = entries.pop(source - 1)
        entries.insert(target - 1, entry)
        updated = PresenceConfig(mode=config.mode, interval_seconds=config.interval_seconds, active_index=min(config.active_index, len(entries) - 1), entries=tuple(entries))
        self.save(updated)
        return updated

    def reorder_entries(self, order: list[int]) -> PresenceConfig:
        config = self.get()
        expected = list(range(1, len(config.entries) + 1))
        if sorted(order) != expected:
            raise ValueError(f"Ordem invalida. Use todos os indices uma vez: {' '.join(str(item) for item in expected)}.")

        active_entry = config.entries[config.active_index]
        entries = tuple(config.entries[index - 1] for index in order)
        active_index = entries.index(active_entry)
        updated = PresenceConfig(mode=config.mode, interval_seconds=config.interval_seconds, active_index=active_index, entries=entries)
        self.save(updated)
        return updated

    def clear(self) -> PresenceConfig:
        config = PresenceConfig(entries=(PresenceEntry(activity_type="none", text="", emoji=None),))
        self.save(config)
        return config


def build_presence(entry: PresenceEntry) -> tuple[discord.Status, discord.BaseActivity | None]:
    normalized_status = _normalize_status(entry.status)
    status = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "invisible": discord.Status.invisible,
    }[normalized_status]

    activity_type = _normalize_activity_type(entry.activity_type)
    if activity_type == "none":
        return status, None
    if activity_type == "custom":
        emoji = discord.PartialEmoji.from_str(entry.emoji) if entry.emoji else None
        return status, discord.CustomActivity(name=entry.text, emoji=emoji)
    if activity_type == "listening":
        return status, discord.Activity(type=discord.ActivityType.listening, name=entry.text)
    if activity_type == "watching":
        return status, discord.Activity(type=discord.ActivityType.watching, name=entry.text)
    if activity_type == "competing":
        return status, discord.Activity(type=discord.ActivityType.competing, name=entry.text)
    return status, discord.Game(name=entry.text)


def format_entry(index: int, entry: PresenceEntry, *, active: bool = False) -> str:
    marker = " <- ativo" if active else ""
    emoji = f"{entry.emoji} " if entry.emoji else ""
    return f"`{index}` {entry.status} | {entry.activity_type} | {emoji}{entry.text or 'sem atividade'}{marker}"


def _normalize_status(status: str) -> str:
    value = str(status).lower()
    aliases = {
        "ausente": "idle",
        "ocupado": "dnd",
        "naoperturbe": "dnd",
        "nao-perturbe": "dnd",
        "invisivel": "invisible",
    }
    value = aliases.get(value, value)
    if value not in VALID_STATUSES:
        raise ValueError("Status invalido. Use online, idle/ausente, dnd/nao-perturbe ou invisible/invisivel.")
    return value


def _normalize_activity_type(activity_type: str) -> str:
    value = str(activity_type).lower()
    aliases = {
        "jogando": "playing",
        "ouvindo": "listening",
        "assistindo": "watching",
        "competindo": "competing",
        "personalizado": "custom",
        "nenhum": "none",
    }
    value = aliases.get(value, value)
    if value not in {"playing", "listening", "watching", "competing", "custom", "none"}:
        raise ValueError("Tipo invalido. Use playing/jogando, listening/ouvindo, watching/assistindo, competing/competindo, custom/personalizado ou none/nenhum.")
    return value


def _clamp_int(value, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
