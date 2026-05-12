from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.workflow import WorkflowRunner


@pytest.mark.asyncio
async def test_runner_cancelled_and_raise_if_cancelled() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    await app.create_job("job1", "/workflow")
    runner = WorkflowRunner(asyncio.Queue(), "sid", app.memory, app, job_id="job1")

    assert await runner.cancelled() is False
    await app.jobs.update_status("job1", "cancelled", error="Job cancelled")
    assert await runner.cancelled() is True
    with pytest.raises(asyncio.CancelledError):
        await runner.raise_if_cancelled()


@pytest.mark.asyncio
async def test_async_workflow_observes_cancel_between_steps() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_step(message: str) -> None:
        pass

    @app.workflow("/long", mode="async")
    async def long(runner: WorkflowRunner) -> str:
        started.set()
        await release.wait()
        await runner.raise_if_cancelled()
        await runner.step("never", wait_step, "input")
        return "done"

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/long", json={})
        body = response.json()
        await asyncio.wait_for(started.wait(), timeout=1)
        cancel = await client.post(f"/__yomai__/jobs/{body['job_id']}/cancel")
        release.set()
        for _ in range(20):
            status = (await client.get(body["status_url"])).json()
            if status["status"] == "cancelled":
                break
            await asyncio.sleep(0.01)
        stream = await client.get(body["stream_url"])

    assert cancel.status_code == 200
    assert status["status"] == "cancelled"
    assert "event: error" in stream.text
    assert '"code":"cancelled"' in stream.text
    assert "event: done" in stream.text


@pytest.mark.asyncio
async def test_cancelled_queued_job_does_not_start_when_worker_later_runs() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    ran = False

    async def handler() -> str:
        nonlocal ran
        ran = True
        return "done"

    await app.create_job("job1", "/queued")
    await app.jobs.update_status("job1", "cancelled", error="Job cancelled")
    await app._run_inline_workflow_job(
        job_id="job1",
        path="/queued",
        handler=handler,
        body={},
        session_id="sid",
        path_kwargs={},
    )

    assert ran is False
    job = await app.jobs.get("job1")
    assert job is not None
    assert job.status == "cancelled"
