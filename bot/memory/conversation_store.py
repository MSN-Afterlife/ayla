from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class MessageEntry:
    author: str
    content: str


class ConversationStore:
    def __init__(self, limit: int) -> None:
        self._messages: dict[str, deque[MessageEntry]] = defaultdict(lambda: deque(maxlen=limit))

    def add(self, conversation_id: str, author: str, content: str) -> None:
        self._messages[conversation_id].append(MessageEntry(author=author, content=content))

    def get_recent(self, conversation_id: str) -> list[MessageEntry]:
        return list(self._messages[conversation_id])

    def clear(self, conversation_id: str) -> None:
        self._messages[conversation_id].clear()
