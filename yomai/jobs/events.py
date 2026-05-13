from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class StoredEvent:
    id: int | str
    event: str
    data: dict[str, Any]


class JobEventStore(Protocol):
    async def append(self, job_id: str, event: str, data: dict[str, Any]) -> int | str: ...

    async def read_after(self, job_id: str, event_id: int | str | None) -> list[StoredEvent]: ...

    async def subscribe(
        self,
        job_id: str,
        after_id: int | str | None = None,
        *,
        heartbeat_secs: float = 15.0,
    ) -> AsyncIterator[StoredEvent | None]: ...


class InMemoryJobEventStore:
    """Append-only job event store for tests and inline/dev queue mode.

    `subscribe()` yields stored events after `after_id`, then live events as they
    arrive. It yields `None` on heartbeat timeouts so callers can emit SSE
    comments/heartbeats without closing the stream.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[StoredEvent]] = defaultdict(list)
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)

    async def append(self, job_id: str, event: str, data: dict[str, Any]) -> int:
        condition = self._conditions[job_id]
        async with condition:
            next_id = len(self._events[job_id]) + 1
            stored = StoredEvent(next_id, event, data)
            self._events[job_id].append(stored)
            condition.notify_all()
            return next_id

    async def read_after(self, job_id: str, event_id: int | str | None) -> list[StoredEvent]:
        try:
            after = int(event_id or 0)
        except (ValueError, TypeError):
            after = 0
        return [event for event in self._events.get(job_id, []) if int(event.id) > after]

    async def subscribe(
        self,
        job_id: str,
        after_id: int | str | None = None,
        *,
        heartbeat_secs: float = 15.0,
    ) -> AsyncIterator[StoredEvent | None]:
        try:
            last_id = int(after_id or 0)
        except (ValueError, TypeError):
            last_id = 0
        while True:
            pending = await self.read_after(job_id, last_id)
            for event in pending:
                last_id = int(event.id)
                yield event

            condition = self._conditions[job_id]
            try:
                async with condition:
                    await asyncio.wait_for(condition.wait(), timeout=heartbeat_secs)
            except TimeoutError:
                yield None


class RedisJobEventStore:
    """Redis Streams-backed job event store for cross-process SSE replay."""

    def __init__(self, url: str, *, prefix: str = "yomai", ttl_secs: int = 86400, client: Any | None = None) -> None:
        self.url = url
        self.prefix = prefix.rstrip(":")
        self.ttl_secs = ttl_secs
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001 - optional dependency guard
                from yomai.exceptions import YomaiConfigError

                raise YomaiConfigError(
                    "Redis job event store requires redis to be installed.",
                    hint="Install Yomai with queue/redis extras or install redis>=5.",
                    docs="https://yomai.dev/roadmap",
                ) from exc
            self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, job_id: str) -> str:
        return f"{self.prefix}:jobs:{job_id}:events"

    async def append(self, job_id: str, event: str, data: dict[str, Any]) -> str:
        key = self._key(job_id)
        event_id = await self.client.xadd(
            key,
            {"event": event, "data": json.dumps(data, separators=(",", ":"))},
        )
        if self.ttl_secs > 0:
            await self.client.expire(key, self.ttl_secs)
        return str(event_id)

    async def read_after(self, job_id: str, event_id: int | str | None) -> list[StoredEvent]:
        key = self._key(job_id)
        start = "-" if event_id is None else f"({event_id}"
        rows = await self.client.xrange(key, min=start, max="+")
        return [self._decode(row_id, fields) for row_id, fields in rows]

    async def subscribe(
        self,
        job_id: str,
        after_id: int | str | None = None,
        *,
        heartbeat_secs: float = 15.0,
    ) -> AsyncIterator[StoredEvent | None]:
        key = self._key(job_id)
        last_id = str(after_id or "0-0")
        while True:
            rows = await self.client.xread({key: last_id}, block=max(1, int(heartbeat_secs * 1000)), count=100)
            if not rows:
                yield None
                continue
            for _stream, events in rows:
                for row_id, fields in events:
                    last_id = str(row_id)
                    yield self._decode(row_id, fields)

    def _decode(self, event_id: str, fields: dict[str, Any]) -> StoredEvent:
        raw_data = fields.get("data", "{}")
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            data = {"type": fields.get("event", "message"), "content": raw_data}
        if not isinstance(data, dict):
            data = {"type": fields.get("event", "message"), "content": data}
        return StoredEvent(str(event_id), str(fields.get("event", data.get("type", "message"))), data)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
