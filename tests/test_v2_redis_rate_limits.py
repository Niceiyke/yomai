from __future__ import annotations

import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.limits import RedisRateLimiter


class FakeRedisLimit:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expired: dict[str, int] = {}
        self.deleted: list[str] = []

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def decr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expired[key] = ttl

    async def ttl(self, key: str) -> int:
        return self.expired.get(key, 60)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    def register_script(self, script: str) -> FakeScript:
        return FakeScript(self, script)


class FakeScript:
    def __init__(self, redis: FakeRedisLimit, script: str) -> None:
        self._redis = redis
        self._script = script

    async def __call__(self, keys: list[str] | None = None, args: list[int] | None = None) -> int:
        keys = keys or []
        args = args or []
        key = keys[0] if keys else ""
        limit = args[0] if args else 0
        current = self._redis.values.get(key, 0) + 1
        self._redis.values[key] = current
        if limit <= 0 or current <= limit:
            return 1
        self._redis.values[key] = current - 1
        return 0


@pytest.mark.asyncio
async def test_redis_rate_limiter_request_limit_and_concurrency() -> None:
    client = FakeRedisLimit()
    limiter = RedisRateLimiter("redis://test", prefix="yomai:test", client=client)

    assert await limiter.check_request("sid", 1, now=60) is None
    assert await limiter.check_request("sid", 1, now=60) == 60

    assert await limiter.acquire_concurrent("sid", 1) is True
    assert await limiter.acquire_concurrent("sid", 1) is False
    await limiter.release_concurrent("sid")
    assert "yomai:test:concurrent:sid" in client.deleted


def test_swiftq_app_uses_redis_rate_limiter() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="swiftq", url="redis://test"),
    )
    assert isinstance(app.rate_limiter, RedisRateLimiter)
