from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest

from yomai import HookEvent, Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.workflow import WorkflowRunner


@pytest.mark.asyncio
async def test_hooks_fire_for_async_workflow_success() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
    )
    seen: list[str] = []

    @app.on("job.queued")
    async def queued(event: HookEvent) -> None:
        seen.append(event.name)

    @app.on("job.succeeded")
    async def succeeded(event: HookEvent) -> None:
        seen.append(f"{event.name}:{event.payload['route']}")

    @app.workflow("/ok", mode="async")
    async def ok(runner: WorkflowRunner) -> str:
        return "done"

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ok", json={})
        body = response.json()
        for _ in range(20):
            status = (await client.get(body["status_url"])).json()
            if status["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)

    assert "job.queued" in seen
    assert "job.succeeded:/ok" in seen


@pytest.mark.asyncio
async def test_metrics_endpoint_counts_jobs() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
    )

    @app.workflow("/ok", mode="async")
    async def ok() -> str:
        return "done"

    @app.workflow("/fail", mode="async")
    async def fail() -> str:
        raise RuntimeError("boom")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ok_resp = await client.post("/ok", json={})
        fail_resp = await client.post("/fail", json={})
        urls = [ok_resp.json()["status_url"], fail_resp.json()["status_url"]]
        for _ in range(30):
            statuses = [(await client.get(url)).json()["status"] for url in urls]
            if "succeeded" in statuses and "failed" in statuses:
                break
            await asyncio.sleep(0.01)
        metrics = await client.get("/__yomai__/metrics")

    data = metrics.json()
    assert data["jobs_total"] == 2
    assert data["jobs_succeeded"] == 1
    assert data["jobs_failed"] == 1
    assert data["workflow_jobs_total"] == 2
    assert data["errors_total"] >= 1
