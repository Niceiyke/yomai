from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.workflow import WorkflowRunner


@pytest.mark.asyncio
async def test_async_workflow_returns_202_and_completes_inline_job() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
    )

    @app.workflow("/research", mode="async")
    async def research(topic: str, runner: WorkflowRunner) -> dict[str, str]:
        return {"topic": topic}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/research", json={"topic": "ai"})
        assert response.status_code == 202
        body = response.json()
        job_id = body["job_id"]

        status: dict[str, Any] = {}
        for _ in range(20):
            status = (await client.get(body["status_url"])).json()
            if status["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)

        stream = await client.get(body["stream_url"])

    assert status["status"] == "succeeded"
    assert status["result"] == {"topic": "ai"}
    assert f"/__yomai__/jobs/{job_id}/stream" == body["stream_url"]
    assert "event: job_queued" in stream.text
    assert "event: result" in stream.text
    assert '"content":"{\\"topic\\":\\"ai\\"}"' in stream.text
    assert "event: done" in stream.text


@pytest.mark.asyncio
async def test_async_workflow_failure_updates_job_and_stream() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
    )

    @app.workflow("/fail", mode="async")
    async def fail(topic: str) -> str:
        raise RuntimeError(f"bad {topic}")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/fail", json={"topic": "input"})
        body = response.json()
        status: dict[str, Any] = {}
        for _ in range(20):
            status = (await client.get(body["status_url"])).json()
            if status["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        stream = await client.get(body["stream_url"])

    assert status["status"] == "failed"
    assert "bad input" in status["error"]
    assert "event: error" in stream.text
    assert "bad input" in stream.text
    assert "event: done" in stream.text
