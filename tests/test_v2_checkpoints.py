from __future__ import annotations

import asyncio

import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig
from yomai.jobs import InMemoryCheckpointStore, RedisCheckpointStore, StepCheckpoint
from yomai.testing import mock_llm
from yomai.workflow import WorkflowRunner


class FakeRedisKV:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = ex


@pytest.mark.asyncio
async def test_in_memory_checkpoint_store_get_save() -> None:
    store = InMemoryCheckpointStore()
    checkpoint = StepCheckpoint(job_id="job1", step="search", input_hash="h", result="ok")
    await store.save(checkpoint)
    assert await store.get("job1", "search", "h") == checkpoint
    assert await store.get("job1", "search", "other") is None


@pytest.mark.asyncio
async def test_redis_checkpoint_store_get_save() -> None:
    client = FakeRedisKV()
    store = RedisCheckpointStore("redis://test", prefix="yomai:test", ttl_secs=60, client=client)
    checkpoint = StepCheckpoint(job_id="job1", step="search", input_hash="h", result={"ok": True})
    await store.save(checkpoint)
    loaded = await store.get("job1", "search", "h")
    assert loaded is not None
    assert loaded.result == {"ok": True}
    assert client.expiries["yomai:test:jobs:job1:checkpoints:search:h"] == 60


@pytest.mark.asyncio
async def test_workflow_runner_step_uses_checkpoint_when_available() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    runner = WorkflowRunner(queue, "sid", app.memory, app, job_id="job1")

    async def agent(message: str) -> None:
        pass

    with mock_llm(["first"]):
        assert await runner.step("search", agent, "input") == "first"

    # If checkpointing works, this second call returns cached result and does
    # not consume the mock LLM response.
    with mock_llm(["should-not-be-used"]):
        assert await runner.step("search", agent, "input") == "first"
