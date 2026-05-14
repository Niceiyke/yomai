from __future__ import annotations

import pytest

from yomai.jobs import JobRecord, RedisJobStore


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expired: list[tuple[str, int]] = []
        self._watched: set[str] = set()
        self._tx: FakeTx | None = None

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes[key] = dict(mapping)
        if self._tx is not None:
            self._tx._commands.append(("hset", key, mapping))

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, set()).add(value)
        if self._tx is not None:
            self._tx._commands.append(("sadd", key, value))

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def expire(self, key: str, ttl: int) -> None:
        self.expired.append((key, ttl))
        if self._tx is not None:
            self._tx._commands.append(("expire", key, ttl))

    async def watch(self, key: str) -> None:
        self._watched.add(key)

    async def unwatch(self) -> None:
        self._watched.clear()

    async def exists(self, key: str) -> bool:
        return key in self.hashes or key in self.sets

    def multi(self) -> FakeTx:
        self._tx = FakeTx(self)
        return self._tx


class FakeTx:
    def __init__(self, parent: FakeRedis) -> None:
        self._parent = parent
        self._commands: list[tuple] = []

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        self._commands.append(("hset", key, mapping))

    def sadd(self, key: str, value: str) -> None:
        self._commands.append(("sadd", key, value))

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._commands.append(("set", key, value, ex or 0))

    def expire(self, key: str, ttl: int) -> None:
        self._commands.append(("expire", key, ttl))

    async def execute(self) -> list | None:
        self._parent._tx = None
        for cmd in self._commands:
            if cmd[0] == "hset":
                self._parent.hashes[cmd[1]] = dict(cmd[2])
            elif cmd[0] == "expire":
                self._parent.expired.append((cmd[1], cmd[2]))
            elif cmd[0] == "sadd":
                self._parent.sets.setdefault(cmd[1], set()).add(cmd[2])
            elif cmd[0] == "set":
                self._parent.hashes[cmd[1]] = {"_set_value": cmd[2]}
        return [True]


@pytest.mark.asyncio
async def test_redis_job_store_create_get_update_and_list() -> None:
    client = FakeRedis()
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
