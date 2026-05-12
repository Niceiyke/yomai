from __future__ import annotations

import pytest

from yomai.jobs import JobRecord, RedisJobStore


class FakeRedisHash:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expired: list[tuple[str, int]] = []

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes[key] = dict(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, set()).add(value)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def expire(self, key: str, ttl: int) -> None:
        self.expired.append((key, ttl))


@pytest.mark.asyncio
async def test_redis_job_store_create_get_update_and_list() -> None:
    client = FakeRedisHash()
    store = RedisJobStore("redis://test", prefix="yomai:test", ttl_secs=60, client=client)
    record = JobRecord(id="job1", route="/research", stream_url="/stream", status_url="/status")

    await store.create(record)
    loaded = await store.get("job1")
    assert loaded is not None
    assert loaded.id == "job1"
    assert loaded.status == "queued"
    assert ("yomai:test:jobs:job1:record", 60) in client.expired

    running = await store.update_status("job1", "running")
    assert running is not None
    assert running.status == "running"
    assert running.attempts == 1
    assert running.started_at is not None

    done = await store.update_status("job1", "succeeded", result={"ok": True})
    assert done is not None
    assert done.status == "succeeded"
    assert done.result == {"ok": True}
    assert done.finished_at is not None

    jobs = list(await store.list())
    assert len(jobs) == 1
    assert jobs[0].id == "job1"


def test_swiftq_app_uses_redis_job_store() -> None:
    from yomai import Yomai
    from yomai.config import LLMConfig, MemoryConfig, QueueConfig

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="swiftq", url="redis://test"),
    )
    assert isinstance(app.jobs, RedisJobStore)
