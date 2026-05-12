from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig, RateLimitConfig


@pytest.mark.asyncio
async def test_async_workflow_requests_per_minute_limit() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
        rate_limits=RateLimitConfig(requests_per_minute=1),
    )

    @app.workflow("/ok", mode="async")
    async def ok() -> str:
        return "ok"

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/ok", json={}, headers={"X-Session-Id": "sid"})
        second = await client.post("/ok", json={}, headers={"X-Session-Id": "sid"})

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["code"] == "rate_limited"
