import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Character:
    name: str
    short_description: str
    personality: list[str]
    style: dict[str, Any]
    boundaries: list[str]
    fallbacks: dict[str, str]


def load_character(file_path: str) -> Character:
    data = json.loads(Path(file_path).read_text(encoding="utf-8"))

    return Character(
        name=data["name"],
        short_description=data["short_description"],
        personality=data.get("personality", []),
        style=data.get("style", {}),
        boundaries=data.get("boundaries", []),
        fallbacks=data.get("fallbacks", {}),
    )
