from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.exceptions import YomaiConfigError
from yomai.queue.base import QueuedWorkflow


def test_swiftq_backend_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "swiftq", None)
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="swiftq"),
    )
    with pytest.raises(YomaiConfigError, match="swiftQ"):
        app._get_queue_backend()


@pytest.mark.asyncio
async def test_swiftq_adapter_enqueues_internal_workflow_task(monkeypatch: pytest.MonkeyPatch) -> None:
    enqueued: dict[str, Any] = {}

    class FakeTask:
        def apply_async(self, **kwargs: Any) -> object:
            enqueued.update(kwargs)
            return object()

    class FakeQueue:
        def __init__(self) -> None:
            self.registered: dict[str, Any] = {}

        @classmethod
        def redis(cls, *args: Any, **kwargs: Any) -> FakeQueue:
            return cls()

        def task(self, **opts: Any):
            def decorator(fn: Any) -> FakeTask:
                self.registered[opts["name"]] = fn
                return FakeTask()
            return decorator

        def work(self, **kwargs: Any) -> None:
            return None

    fake_module = types.SimpleNamespace(Queue=FakeQueue)
    monkeypatch.setitem(sys.modules, "swiftq", fake_module)

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="swiftq", url="redis://test"),
    )
    backend = app._get_queue_backend()
    assert backend is not None, "Backend should be created with swiftQ configured"
    await backend.enqueue_workflow(
        QueuedWorkflow(
            job_id="job1",
            route="/research",
            payload={"topic": "ai"},
            session_id="sid",
            metadata={"path_kwargs": {"team": "core"}},
        )
    )

    assert enqueued["unique_key"] == "yomai:workflow:job1"
    assert enqueued["kwargs"] == {
        "job_id": "job1",
        "path": "/research",
        "body": {"topic": "ai"},
        "session_id": "sid",
        "path_kwargs": {"team": "core"},
    }
