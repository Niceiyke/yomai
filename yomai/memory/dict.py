from __future__ import annotations

import asyncio

from yomai.llm.base import Message
from yomai.memory.base import MemoryBackend


class DictMemory(MemoryBackend):
    """In-process memory backend for V1.

    This backend is not persisted across process restarts and is intended for
    development, tests, and small single-process deployments.
    """

    def __init__(self, max_messages: int = 20) -> None:
        self._store: dict[str, list[Message]] = {}
        self._max = max_messages
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> list[Message]:
        async with self._lock:
            return list(self._store.get(session_id, []))

    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        async with self._lock:
            history = list(self._store.get(session_id, []))
            history.append({"role": "user", "content": user_message})
            if assistant_reply:
                history.append({"role": "assistant", "content": assistant_reply})

            self._store[session_id] = self._truncate(history)

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            self._store.pop(session_id, None)

    def _truncate(self, history: list[Message]) -> list[Message]:
        if self._max <= 0 or len(history) <= self._max:
            return history

        first = history[0]
        if first.get("role") == "system" and self._max > 1:
            return [first, *history[-(self._max - 1):]]
        return history[-self._max:]
