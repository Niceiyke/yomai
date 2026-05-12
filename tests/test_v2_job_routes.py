from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig


@pytest.mark.asyncio
async def test_job_status_endpoint_returns_job_record() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    await app.create_job("job1", "/research")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__yomai__/jobs/job1")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "job1"
    assert data["status"] == "queued"
    assert data["stream_url"] == "/__yomai__/jobs/job1/stream"


@pytest.mark.asyncio
async def test_job_stream_replays_events_with_ids() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    await app.create_job("job1", "/research")
    await app.job_events.append("job1", "chunk", {"type": "chunk", "content": "one"})
    await app.job_events.append("job1", "chunk", {"type": "chunk", "content": "two"})
    await app.job_events.append("job1", "done", {"type": "done"})

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__yomai__/jobs/job1/stream", headers={"Last-Event-ID": "1"})

    assert response.status_code == 200
    text = response.text
    assert "id: 1" not in text
    assert "id: 2" in text
    assert 'data: {"type":"chunk","content":"two"}' in text
    assert "event: done" in text


@pytest.mark.asyncio
async def test_job_cancel_updates_status_and_emits_done() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    await app.create_job("job1", "/research")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        cancel = await client.post("/__yomai__/jobs/job1/cancel")
        stream = await client.get("/__yomai__/jobs/job1/stream")

    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    assert '"code":"cancelled"' in stream.text
    assert "event: done" in stream.text
