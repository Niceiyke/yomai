from __future__ import annotations

import json

import pytest

from yomai.jobs import RedisJobEventStore


class FakeRedisStream:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, str]]] = []
        self.expired: list[tuple[str, int]] = []

    async def xadd(self, key: str, fields: dict[str, str]) -> str:
        event_id = f"{len(self.rows) + 1}-0"
        self.rows.append((event_id, fields))
        return event_id

    async def expire(self, key: str, ttl: int) -> None:
        self.expired.append((key, ttl))

    async def xrange(self, key: str, min: str = "-", max: str = "+") -> list[tuple[str, dict[str, str]]]:
        if min == "-":
            return list(self.rows)
        if min.startswith("("):
            after = min[1:]
            return [(event_id, fields) for event_id, fields in self.rows if event_id > after]
        return list(self.rows)

    async def xread(self, streams: dict[str, str], block: int, count: int) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        key, after = next(iter(streams.items()))
        rows = [(event_id, fields) for event_id, fields in self.rows if event_id > after]
        return [] if not rows else [(key, rows[:count])]


@pytest.mark.asyncio
async def test_redis_job_event_store_append_and_replay() -> None:
    client = FakeRedisStream()
    store = RedisJobEventStore("redis://test", prefix="yomai:test", ttl_secs=60, client=client)

    event_id = await store.append("job1", "chunk", {"type": "chunk", "content": "one"})
    assert event_id == "1-0"
    assert client.expired == [("yomai:test:jobs:job1:events", 60)]
    assert json.loads(client.rows[0][1]["data"])["content"] == "one"

    await store.append("job1", "done", {"type": "done"})
    replay = await store.read_after("job1", "1-0")
    assert len(replay) == 1
    assert replay[0].id == "2-0"
    assert replay[0].event == "done"


@pytest.mark.asyncio
async def test_redis_job_event_store_subscribe_reads_after_id() -> None:
    client = FakeRedisStream()
    store = RedisJobEventStore("redis://test", client=client)
    await store.append("job1", "chunk", {"type": "chunk", "content": "one"})
    await store.append("job1", "chunk", {"type": "chunk", "content": "two"})

    async for event in store.subscribe("job1", "1-0", heartbeat_secs=0.01):
        assert event is not None
        break
    assert event.id == "2-0"
    assert event.data["content"] == "two"


def test_swiftq_app_uses_redis_job_event_store() -> None:
    from yomai import Yomai
    from yomai.config import LLMConfig, MemoryConfig, QueueConfig

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="swiftq", url="redis://test"),
    )
    assert isinstance(app.job_events, RedisJobEventStore)
