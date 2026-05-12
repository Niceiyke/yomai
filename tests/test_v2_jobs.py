from __future__ import annotations

import asyncio

import pytest

from yomai.jobs import InMemoryJobEventStore, JobRecord


@pytest.mark.asyncio
async def test_in_memory_job_events_append_and_replay() -> None:
    store = InMemoryJobEventStore()
    assert await store.append("job1", "step_start", {"type": "step_start", "name": "a"}) == 1
    assert await store.append("job1", "step_done", {"type": "step_done", "name": "a"}) == 2

    events = await store.read_after("job1", 1)
    assert len(events) == 1
    assert events[0].id == 2
    assert events[0].event == "step_done"


@pytest.mark.asyncio
async def test_in_memory_job_events_subscribe_receives_live_event() -> None:
    store = InMemoryJobEventStore()
    seen = []

    async def consume() -> None:
        async for event in store.subscribe("job1", heartbeat_secs=0.05):
            if event is not None:
                seen.append(event)
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await store.append("job1", "chunk", {"type": "chunk", "content": "hi"})
    await asyncio.wait_for(task, timeout=1)

    assert seen[0].id == 1
    assert seen[0].data["content"] == "hi"


def test_job_record_to_dict_serializes_datetimes() -> None:
    record = JobRecord(id="job1", route="/research", stream_url="/stream", status_url="/status")
    data = record.to_dict()
    assert data["id"] == "job1"
    assert data["status"] == "queued"
    assert data["created_at"].endswith("+00:00")
