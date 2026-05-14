from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from yomai.llm.base import Message
from yomai.memory.base import MemoryBackend


class DictMemory(MemoryBackend):
    """In-process memory backend for V1.

    This backend is not persisted across process restarts and is intended for
    development, tests, and small single-process deployments.
    """

    _EVICT_SAMPLE = 100
    _FULL_EVICT_INTERVAL = 600.0

    def __init__(self, max_messages: int = 20, ttl_hours: int = 24) -> None:
        self._store: OrderedDict[str, tuple[float, list[Message]]] = OrderedDict()
        self._max = max_messages
        self._ttl_secs = max(0, ttl_hours) * 3600
        self._lock = asyncio.Lock()
        self._last_full_evict = time.monotonic()

    async def load(self, session_id: str) -> list[Message]:
        async with self._lock:
            self._evict_sample()
            entry = self._store.get(session_id)
            if entry is None:
                return []
            now = time.monotonic()
            if self._ttl_secs > 0 and now - entry[0] > self._ttl_secs:
                del self._store[session_id]
                return []
            self._store.move_to_end(session_id)
            return list(entry[1])

    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        async with self._lock:
            self._evict_sample()
            entry = self._store.get(session_id)
            history = list(entry[1]) if entry else []
            history.append({"role": "user", "content": user_message})
            if assistant_reply:
                history.append({"role": "assistant", "content": assistant_reply})

            self._store[session_id] = (time.monotonic(), self._truncate(history))
            self._store.move_to_end(session_id)

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            self._store.pop(session_id, None)

    def _evict_sample(self) -> None:
        if self._ttl_secs <= 0:
            return
        now = time.monotonic()
        if now - self._last_full_evict > self._FULL_EVICT_INTERVAL:
            self._evict_expired_full(now)
            self._last_full_evict = now
        else:
            self._evict_expired_partial(now)

    def _evict_expired(self) -> None:
        self._evict_sample()

    def _evict_expired_partial(self, now: float) -> None:
        expired: list[str] = []
        for sid, (updated_at, _) in self._store.items():
            if now - updated_at > self._ttl_secs:
                expired.append(sid)
            if len(expired) >= self._EVICT_SAMPLE:
                break
        for sid in expired:
            del self._store[sid]

    def _evict_expired_full(self, now: float) -> None:
        expired = [sid for sid, (updated_at, _) in self._store.items() if now - updated_at > self._ttl_secs]
        for sid in expired:
            del self._store[sid]

    def _truncate(self, history: list[Message]) -> list[Message]:
        if self._max <= 0 or len(history) <= self._max:
            return history

        first = history[0]
        if first.get("role") == "system" and self._max > 1:
            return [first, *history[-(self._max - 1) :]]
        return history[-self._max :]
