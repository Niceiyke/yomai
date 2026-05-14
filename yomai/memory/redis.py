from __future__ import annotations

import asyncio
import json
from typing import Any

from yomai.exceptions import YomaiConfigError
from yomai.llm.base import Message
from yomai.memory.base import MemoryBackend


class RedisMemory(MemoryBackend):
    """Redis-backed session memory for horizontally scaled Yomai apps."""

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        max_messages: int = 20,
        ttl_hours: int = 24,
        prefix: str = "yomai:memory",
        client: Any | None = None,
    ) -> None:
        self.url = url
        self._max = max_messages
        self._ttl_secs = max(0, ttl_hours) * 3600
        self._prefix = prefix.rstrip(":")
        self._client = client
        self._lock: Any = asyncio.Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from redis import asyncio as redis_asyncio  # type: ignore[import-not-found, unused-ignore]
            except Exception as exc:  # noqa: BLE001 - optional dependency guard
                raise YomaiConfigError(
                    "Redis memory backend requires redis to be installed.",
                    hint="Install Yomai with redis extras or install redis>=5.",
                    docs="https://yomai.dev/roadmap",
                ) from exc
            self._client = redis_asyncio.from_url(self.url, decode_responses=True)  # pyright: ignore[reportAttributeAccessIssue]
        return self._client

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:sessions:{session_id}"

    async def load(self, session_id: str) -> list[Message]:
        raw = await self.client.get(self._key(session_id))
        if not raw:
            return []
        try:
            history = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(history, list):
            return []
        return [msg for msg in history if isinstance(msg, dict)]

    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        async with self._lock:
            history = list(await self.load(session_id))
            history.append({"role": "user", "content": user_message})
            if assistant_reply:
                history.append({"role": "assistant", "content": assistant_reply})
            await self._save_history(session_id, self._truncate(history))

    async def clear(self, session_id: str) -> None:
        await self.client.delete(self._key(session_id))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _save_history(self, session_id: str, history: list[Message]) -> None:
        payload = json.dumps(history, separators=(",", ":"))
        key = self._key(session_id)
        if self._ttl_secs > 0:
            await self.client.set(key, payload, ex=self._ttl_secs)
        else:
            await self.client.set(key, payload)

    def _truncate(self, history: list[Message]) -> list[Message]:
        if self._max <= 0 or len(history) <= self._max:
            return history
        first = history[0]
        if first.get("role") == "system" and self._max > 1:
            return [first, *history[-(self._max - 1) :]]
        return history[-self._max :]
