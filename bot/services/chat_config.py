import json
from pathlib import Path


class ChatConfigStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._data = self._load()

    def get_channel_id(self, guild_id: int) -> int | None:
        value = self._data.get(str(guild_id), {}).get("channel_id")
        return int(value) if value else None

    def set_channel_id(self, guild_id: int, channel_id: int) -> None:
        self._data[str(guild_id)] = {"channel_id": channel_id}
        self._save()

    def clear_channel_id(self, guild_id: int) -> None:
        self._data.pop(str(guild_id), None)
        self._save()

    def _load(self) -> dict[str, dict[str, int]]:
        if not self._path.exists():
            return {}

        with self._path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as file:
            json.dump(self._data, file, indent=2)
