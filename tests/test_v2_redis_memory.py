from __future__ import annotations

import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig
from yomai.memory import RedisMemory


class FakeRedisKV:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = ex

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)
        self.expiries.pop(key, None)


@pytest.mark.asyncio
async def test_redis_memory_save_load_truncate_ttl_and_clear() -> None:
    client = FakeRedisKV()
    mem = RedisMemory(
        "redis://test",
        max_messages=3,
        ttl_hours=1,
        prefix="yomai:test",
        client=client,
    )

    await mem.save("s1", "u1", "a1")
    await mem.save("s1", "u2", "a2")
    history = await mem.load("s1")

    assert len(history) == 3
    assert history[0] == {"role": "assistant", "content": "a1"}
    assert history[-1] == {"role": "assistant", "content": "a2"}
    assert client.expiries["yomai:test:sessions:s1"] == 3600

    await mem.clear("s1")
    assert await mem.load("s1") == []
    assert client.deleted == ["yomai:test:sessions:s1"]


@pytest.mark.asyncio
async def test_redis_memory_preserves_system_message_when_truncating() -> None:
    client = FakeRedisKV()
    mem = RedisMemory("redis://test", max_messages=3, ttl_hours=0, client=client)
    await mem._save_history(
        "s1",
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old reply"},
        ],
    )
    await mem.save("s1", "new", "reply")
    history = await mem.load("s1")
    assert history == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "reply"},
    ]


def test_yomai_builds_redis_memory_backend() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="redis", url="redis://test", prefix="custom", max_messages=7),
    )
    assert isinstance(app.memory, RedisMemory)
    assert app.memory.url == "redis://test"
    assert app.memory._max == 7
    assert app.memory._prefix == "custom"
