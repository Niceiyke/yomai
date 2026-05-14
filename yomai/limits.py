from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

from yomai.exceptions import YomaiConfigError

# Lua script for atomic concurrent acquire: increment, check limit, return 1 on success or 0 on failure
_ACQUIRE_CONCURRENT_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local current = redis.call('INCR', key)
redis.call('EXPIRE', key, 3600)
if limit <= 0 or current <= limit then
    return 1
end
redis.call('DECR', key)
return 0
"""


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._concurrent: dict[str, int] = defaultdict(int)

    async def check_request(self, key: str, limit: int | None, *, now: float | None = None) -> int | None:
        if not limit or limit <= 0:
            return None
        async with self._lock:
            current = time.time() if now is None else now
            bucket = self._requests[key]
            while bucket and current - bucket[0] >= 60:
                bucket.popleft()
            if not bucket:
                del self._requests[key]
            else:
                if len(bucket) >= limit:
                    retry_after = max(1, int(60 - (current - bucket[0])))
                    return retry_after
            bucket.append(current)
            self._requests[key] = bucket
            return None

    async def acquire_concurrent(self, key: str, limit: int | None) -> bool:
        async with self._lock:
            if not limit or limit <= 0:
                self._concurrent[key] += 1
                return True
            if self._concurrent[key] >= limit:
                return False
            self._concurrent[key] += 1
            return True

    async def release_concurrent(self, key: str) -> None:
        async with self._lock:
            self._concurrent[key] = max(0, self._concurrent[key] - 1)
            if self._concurrent[key] <= 0:
                del self._concurrent[key]


class RedisRateLimiter:
    """Redis-backed rate limiter for horizontally scaled Yomai apps."""

    def __init__(self, url: str, *, prefix: str = "yomai:limits", client: Any | None = None) -> None:
        self.url = url
        self.prefix = prefix.rstrip(":")
        self._client = client
        self._acquire_script: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                raise YomaiConfigError(
                    "Redis rate limiter requires redis to be installed.",
                    hint="Install Yomai with queue/redis extras or install redis>=5.",
                    docs="https://yomai.dev/roadmap",
                ) from exc
            self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    async def _get_acquire_script(self) -> Any:
        if self._acquire_script is None:
            self._acquire_script = self.client.register_script(_ACQUIRE_CONCURRENT_SCRIPT)
        return self._acquire_script

    def _request_key(self, key: str, now: float | None = None) -> str:
        minute = int((time.time() if now is None else now) // 60)
        return f"{self.prefix}:requests:{key}:{minute}"

    def _concurrent_key(self, key: str) -> str:
        return f"{self.prefix}:concurrent:{key}"

    async def check_request(self, key: str, limit: int | None, *, now: float | None = None) -> int | None:
        if not limit or limit <= 0:
            return None
        redis_key = self._request_key(key, now)
        count = await self.client.incr(redis_key)
        if int(count) == 1:
            await self.client.expire(redis_key, 60)
        if int(count) > limit:
            ttl = await self.client.ttl(redis_key)
            return max(1, int(ttl if ttl and ttl > 0 else 60))
        return None

    async def acquire_concurrent(self, key: str, limit: int | None) -> bool:
        redis_key = self._concurrent_key(key)
        effective_limit = limit if limit and limit > 0 else 0
        script = await self._get_acquire_script()
        result = int(await script(keys=[redis_key], args=[effective_limit]))
        return result == 1

    async def release_concurrent(self, key: str) -> None:
        redis_key = self._concurrent_key(key)
        value = int(await self.client.decr(redis_key))
        if value <= 0:
            await self.client.delete(redis_key)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
