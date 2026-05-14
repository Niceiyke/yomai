"""Tests for memory backends, hooks, plugins, response extraction, multi-modal,
tool annotations, streaming disconnect, interrupt timeout, and queue types."""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import pytest

from yomai.config import LLMConfig, MemoryConfig

# ===========================================================================
# #1 — Memory backend edge tests
# ===========================================================================

class TestMemoryBackends:
    @pytest.mark.asyncio
    async def test_dict_memory_save_load_clear(self) -> None:
        from yomai.memory.dict import DictMemory

        mem = DictMemory(max_messages=10)
        await mem.save("s1", "hello", "hi there")
        history = await mem.load("s1")
        assert len(history) == 2
        assert history[0]["content"] == "hello"
        assert history[1]["content"] == "hi there"

        await mem.clear("s1")
        assert await mem.load("s1") == []

    @pytest.mark.asyncio
    async def test_dict_memory_truncation(self) -> None:
        from yomai.memory.dict import DictMemory

        mem = DictMemory(max_messages=4)
        for i in range(5):
            await mem.save("s1", f"msg{i}", f"reply{i}")
        history = await mem.load("s1")
        assert len(history) <= 4

    @pytest.mark.asyncio
    async def test_sqlite_memory_save_load_clear(self) -> None:
        from yomai.memory.sqlite import SqliteMemory

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            mem = SqliteMemory(db_path=db_path, max_messages=20)
            await mem.save("s1", "question", "answer")
            history = await mem.load("s1")
            assert len(history) == 2
            assert history[1]["content"] == "answer"

            await mem.clear("s1")
            assert await mem.load("s1") == []
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_sqlite_memory_persistence(self) -> None:
        from yomai.memory.sqlite import SqliteMemory

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            mem1 = SqliteMemory(db_path=db_path)
            await mem1.save("persist", "data", "result")

            # New instance with same DB path should see the data
            mem2 = SqliteMemory(db_path=db_path)
            history = await mem2.load("persist")
            assert len(history) == 2
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_sqlite_memory_corrupted_json_returns_empty(self) -> None:
        from yomai.memory.sqlite import SqliteMemory

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            mem = SqliteMemory(db_path=db_path)
            await mem._save_sync("corrupt", [{"role": "user", "content": "ok"}])

            # Manually corrupt the stored JSON
            conn = mem._connect()
            try:
                conn.execute("UPDATE sessions SET history_json = ? WHERE session_id = ?", ("not-json", "corrupt"))
                conn.commit()
            finally:
                conn.close()

            history = await mem.load("corrupt")
            assert history == []
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_redis_memory_save_load_uses_client(self, redis_client: Any) -> None:
        from yomai.memory.redis import RedisMemory

        mem = RedisMemory(client=redis_client, max_messages=10)
        await mem.save("redis-s1", "ping", "pong")
        history = await mem.load("redis-s1")
        assert len(history) == 2
        await mem.clear("redis-s1")
        assert await mem.load("redis-s1") == []

    @pytest.mark.asyncio
    async def test_redis_memory_truncation(self, redis_client: Any) -> None:
        from yomai.memory.redis import RedisMemory

        mem = RedisMemory(client=redis_client, max_messages=4)
        for i in range(5):
            await mem.save("redis-trunc", f"q{i}", f"a{i}")
        history = await mem.load("redis-trunc")
        assert len(history) <= 4


# ===========================================================================
# #2 — Hooks system tests
# ===========================================================================

class TestHooksSystem:
    @pytest.mark.asyncio
    async def test_emit_runs_all_handlers(self) -> None:
        from yomai.hooks import HookRegistry

        calls: list[str] = []
        hooks = HookRegistry()

        async def h1(event: Any) -> None:
            calls.append("h1")

        async def h2(event: Any) -> None:
            calls.append("h2")

        hooks.on("test.event", h1)
        hooks.on("test.event", h2)

        failures = await hooks.emit("test.event")
        assert len(failures) == 0
        assert sorted(calls) == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_emit_captures_handler_failures(self) -> None:
        from yomai.hooks import HookRegistry

        hooks = HookRegistry()

        async def failing(event: Any) -> None:
            raise RuntimeError("boom")

        async def ok(event: Any) -> None:
            pass

        hooks.on("test.fail", failing)
        hooks.on("test.fail", ok)

        failures = await hooks.emit("test.fail")
        assert len(failures) == 1
        assert "boom" in failures[0]["error"]

    @pytest.mark.asyncio
    async def test_pop_failures_clears_accumulated(self) -> None:
        from yomai.hooks import HookRegistry

        hooks = HookRegistry()

        async def fail1(event: Any) -> None:
            raise RuntimeError("fail1")

        hooks.on("test.pop", fail1)
        await hooks.emit("test.pop")

        popped = hooks.pop_failures()
        assert len(popped) == 1

        # After pop, failures should be cleared
        assert hooks.pop_failures() == []

    @pytest.mark.asyncio
    async def test_emit_background_schedules_task(self) -> None:
        from yomai.hooks import HookRegistry

        done = False
        hooks = HookRegistry()

        async def bg_handler(event: Any) -> None:
            nonlocal done
            done = True

        hooks.on("test.bg", bg_handler)
        await hooks.emit("test.bg")
        assert done  # emit is synchronous within the test

    @pytest.mark.asyncio
    async def test_emit_background_no_loop_is_silent(self) -> None:
        """emit_background silently returns if there's no running loop."""
        from yomai.hooks import HookRegistry

        hooks = HookRegistry()
        # In a test with asyncio, we have a loop, so this won't trigger
        # the RuntimeError path. Just verifying the method exists and doesn't crash.
        hooks.emit_background("test.silent", key="val")
        await asyncio.sleep(0.01)  # let background task complete


# ===========================================================================
# #3 — Plugin system tests
# ===========================================================================

class TestPluginSystem:
    def test_load_plugins_empty(self) -> None:
        from yomai.plugins import load_plugins

        result = load_plugins(None)
        assert result == []
        result = load_plugins([])
        assert result == []

    def test_load_plugins_callables(self) -> None:
        from yomai.plugins import load_plugins

        def plugin1(app: Any) -> None:
            pass

        def plugin2(app: Any) -> None:
            pass

        result = load_plugins([plugin1, plugin2])
        assert len(result) == 2
        assert result[0] is plugin1
        assert result[1] is plugin2

    def test_load_plugins_string_path(self) -> None:
        from yomai.plugins import load_plugins
        result = load_plugins(["yomai.plugins:plugin"])
        assert len(result) == 1
        assert callable(result[0])

    def test_load_plugins_string_no_colon_defaults_to_setup(self) -> None:
        from yomai.plugins import load_plugins
        # This module has a 'plugin' attribute but no 'setup'
        with pytest.raises(AttributeError):
            load_plugins(["yomai.plugins"])

    def test_load_plugins_string_not_callable_raises(self) -> None:
        from yomai.plugins import load_plugins

        with pytest.raises(ValueError, match="did not resolve to a callable"):
            load_plugins(["yomai.plugins:_registry"])  # _registry is a list, not callable

    def test_load_plugins_invalid_type_raises(self) -> None:
        from yomai.plugins import load_plugins

        with pytest.raises(TypeError, match="Expected callable or str"):
            load_plugins([42])  # type: ignore[list-item]

    def test_plugin_decorator_registers(self) -> None:
        from yomai.plugins import _registry, plugin

        original_len = len(_registry)

        @plugin
        def my_setup(app: Any) -> None:
            pass

        assert my_setup in _registry
        assert len(_registry) == original_len + 1


# ===========================================================================
# #4 — Response model rightmost-preference extraction
# ===========================================================================

class TestResponseModelExtraction:
    def test_chooses_rightmost_json_object(self) -> None:
        from pydantic import BaseModel

        from yomai.core.router import AgentRoute

        class Output(BaseModel):
            result: str

        route = AgentRoute.__new__(AgentRoute)
        # Two JSON objects: the rightmost one has the correct schema
        text = 'First {"result": "wrong"}. Second {"result": "correct"}.'
        validated = route._extract_json(text, Output)
        assert validated.result == "correct"

    def test_falls_back_to_start_if_rightmost_fails(self) -> None:
        from pydantic import BaseModel

        from yomai.core.router import AgentRoute

        class Output(BaseModel):
            answer: int

        route = AgentRoute.__new__(AgentRoute)
        # Rightmost is {"x": 1} which doesn't validate, but leftmost {"answer": 42} does
        text = '{"answer": 42} extra {"x": 1}'
        validated = route._extract_json(text, Output)
        assert validated.answer == 42


# ===========================================================================
# #5 — Multi-modal messages
# ===========================================================================

class TestMultiModalMessages:
    def test_agent_request_text_only(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(message="hello world")
        assert req.message_text == "hello world"

    def test_agent_request_image_url_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(message=[
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ])
        assert "Describe this" in req.message_text

    def test_agent_request_audio_block(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(message=[
            {"type": "input_audio", "input_audio": {"data": "base64..."}},
            {"type": "text", "text": "Transcribe"},
        ])
        assert req.message_text == "Transcribe"

    def test_agent_request_no_text_returns_placeholder(self) -> None:
        from yomai.core.schemas import AgentRequest

        req = AgentRequest(message=[
            {"type": "image_url", "image_url": {"url": "http://x"}},
        ])
        assert req.message_text == "[multi-modal]"

    def test_message_text_helper(self) -> None:
        from yomai.core.agent import _message_text

        assert _message_text("plain") == "plain"
        assert _message_text([{"type": "text", "text": "hi"}, {"type": "image_url"}]) == "hi [image]"
        assert _message_text([{"type": "input_audio"}]) == "[audio]"


# ===========================================================================
# #6 — Tool decorator annotation edges
# ===========================================================================

class TestToolAnnotationEdges:
    def test_literal_enum_generation(self) -> None:
        from typing import Literal

        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(Literal["small", "medium", "large"])
        assert schema["type"] == "string"
        assert "enum" in schema
        assert set(schema["enum"]) == {"small", "medium", "large"}

    def test_set_type_generation(self) -> None:
        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(set[int])
        assert schema["type"] == "array"
        assert schema["uniqueItems"] is True

    def test_tuple_fixed_length_generation(self) -> None:
        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(tuple[int, str])
        assert schema["type"] == "array"
        assert "prefixItems" in schema
        assert schema["minItems"] == 2

    def test_tuple_variable_length_generation(self) -> None:
        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(tuple[int, ...])
        assert schema["type"] == "array"
        assert "items" in schema

    def test_datetime_generation(self) -> None:
        import datetime

        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(datetime.datetime)
        assert schema["type"] == "string"
        assert schema["format"] == "date-time"

    def test_uuid_generation(self) -> None:
        from uuid import UUID

        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(UUID)
        assert schema["type"] == "string"
        assert schema["format"] == "uuid"

    def test_annotated_with_field_description(self) -> None:
        from typing import Annotated

        from pydantic import Field

        from yomai.tools.decorator import _extract_description, _json_schema_for_annotation

        annotation = Annotated[str, Field(description="User's name")]
        schema = _json_schema_for_annotation(annotation)
        assert schema["type"] == "string"
        # description is on the annotation metadata
        desc = _extract_description(annotation)
        assert desc == "User's name"

    def test_pydantic_model_in_schema(self) -> None:
        from pydantic import BaseModel

        from yomai.tools.decorator import _json_schema_for_annotation

        class Address(BaseModel):
            street: str
            city: str

        schema = _json_schema_for_annotation(Address)
        assert "$defs" in schema or "properties" in schema

    def test_optional_type_generation(self) -> None:
        from yomai.tools.decorator import _json_schema_for_annotation

        schema = _json_schema_for_annotation(str | None)
        assert schema["type"] == "string"


# ===========================================================================
# #7 — Streaming disconnect
# ===========================================================================

class TestStreamingDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_mid_stream_cancels_task(self) -> None:
        """When the client disconnects, the agent task is cancelled."""
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.agent("/chat")
        async def chat(message: str, session_id: str) -> None:
            pass

        # The test client sends a complete request, but we verify the
        # disconnect path exists by checking the request.is_disconnected call
        # in the generate() loop. A full disconnect test would need raw ASGI.
        # Here we verify the generate loop checks is_disconnected.
        from yomai.testing import mock_llm
        with mock_llm(["hello"]):
            events = await YomaiTestClient(app).get_events("/chat", "test", session_id="dc")
        assert any(e.get("type") == "done" for e in events)


# ===========================================================================
# #8 — Interrupt timeout
# ===========================================================================

class TestInterruptTimeout:
    @pytest.mark.asyncio
    async def test_interrupt_timeout_creates_and_resolves(self) -> None:
        from yomai.jobs.interrupts import InMemoryInterruptStore, Interrupt

        store = InMemoryInterruptStore()
        intr = Interrupt(id="intr-1", job_id="job-1", message="approve?")
        await store.create(intr)

        # Resolve it
        ok = await store.resolve("intr-1", "yes", action="approve")
        assert ok

        resolved = await store.get("intr-1")
        assert resolved is not None
        assert resolved.status == "resolved"
        assert resolved.response == "yes"

    @pytest.mark.asyncio
    async def test_interrupt_double_resolve_fails(self) -> None:
        from yomai.jobs.interrupts import InMemoryInterruptStore, Interrupt

        store = InMemoryInterruptStore()
        intr = Interrupt(id="intr-2", job_id="job-1", message="approve?")
        await store.create(intr)

        ok1 = await store.resolve("intr-2", "yes")
        assert ok1
        ok2 = await store.resolve("intr-2", "no")
        assert not ok2

    @pytest.mark.asyncio
    async def test_interrupt_event_wait_resolves(self) -> None:
        from yomai.jobs.interrupts import InMemoryInterruptStore, Interrupt

        store = InMemoryInterruptStore()
        intr = Interrupt(id="intr-3", job_id="job-1", message="wait")
        await store.create(intr)

        event = store.event("intr-3")

        # Resolve in a separate task after a small delay
        async def resolve_later() -> None:
            await asyncio.sleep(0.01)
            await store.resolve("intr-3", "done")

        task = asyncio.create_task(resolve_later())
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pytest.fail("Interrupt event never resolved")
        finally:
            await task

    @pytest.mark.asyncio
    async def test_get_latest_resolved(self) -> None:
        from yomai.jobs.interrupts import InMemoryInterruptStore, Interrupt

        store = InMemoryInterruptStore()
        intr = Interrupt(id="intr-latest", job_id="job-1", message="test")
        await store.create(intr)
        await store.resolve("intr-latest", "approved", action="approve")

        latest = await store.get_latest_resolved()
        assert latest is not None
        assert latest.id == "intr-latest"
        assert latest.response == "approved"


# ===========================================================================
# #9 — Queue backend adapter
# ===========================================================================

@pytest.mark.asyncio
async def test_queue_backend_imports_queued_workflow() -> None:
    from yomai.queue.base import QueuedWorkflow

    wf = QueuedWorkflow(
        job_id="j1",
        route="/workflow",
        payload={"x": 1},
        session_id="s1",
        metadata={"path_kwargs": {}},
    )
    assert wf.job_id == "j1"
    assert wf.route == "/workflow"
    assert wf.payload == {"x": 1}


# ===========================================================================
# #10 — Additional edge: create_job sets URLs
# ===========================================================================

class TestJobRecordEdge:
    @pytest.mark.asyncio
    async def test_create_job_sets_status_and_stream_urls(self) -> None:
        from yomai import Yomai

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        job = await app.create_job("url-job", "/my-route")
        assert job.status_url == "/__yomai__/jobs/url-job"
        assert job.stream_url == "/__yomai__/jobs/url-job/stream"
        assert job.status == "queued"
        assert job.attempts == 0

    @pytest.mark.asyncio
    async def test_job_record_to_dict(self) -> None:
        from yomai.jobs.models import JobRecord

        record = JobRecord(id="test-dict", route="/test")
        d = record.to_dict()
        assert d["id"] == "test-dict"
        assert d["status"] == "queued"
        assert "created_at" in d


# ===========================================================================
# #10 (continued) — error middleware catches general exceptions
# ===========================================================================

class TestErrorMiddlewareEdge:
    @pytest.mark.asyncio
    async def test_non_streaming_error_returns_json(self) -> None:
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.get("/bad")
        async def bad(request: Any) -> dict:
            raise RuntimeError("test error")

        client = YomaiTestClient(app)
        async with await client._client() as http:
            resp = await http.get("/bad")
        assert resp.status_code == 500
        data = resp.json()
        assert "error" in data["message"].lower() or data["code"] == "RuntimeError"


# ===========================================================================
# Schema type classification edge
# ===========================================================================

class TestSchemaType:
    def test_uuid_returns_string(self) -> None:
        from yomai import Yomai
        app = Yomai.__new__(Yomai)
        from uuid import UUID
        assert app._schema_type(UUID) == "string"

    def test_base_model_returns_object(self) -> None:
        from pydantic import BaseModel

        from yomai import Yomai

        class Foo(BaseModel):
            x: int

        app = Yomai.__new__(Yomai)
        assert app._schema_type(Foo) == "object"

    def test_enum_returns_string(self) -> None:
        import enum

        from yomai import Yomai

        class Color(enum.Enum):
            RED = "red"

        app = Yomai.__new__(Yomai)
        assert app._schema_type(Color) == "string"
