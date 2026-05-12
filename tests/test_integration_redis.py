"""Integration tests for Redis-backed functionality.

These tests require a running Redis instance. They will be skipped if Redis
is not available or if TEST_REDIS_URL is set to "none".
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from yomai import Yomai, tool
from yomai.config import LLMConfig, MemoryConfig
from yomai.jobs import JobRecord, RedisCheckpointStore, RedisJobEventStore, RedisJobStore
from yomai.memory import RedisMemory
from yomai.testing import YomaiTestClient, mock_llm


class TestRedisJobStoreIntegration:
    """Integration tests for RedisJobStore with real Redis."""

    @pytest.mark.asyncio
    async def test_create_and_retrieve_job(self, redis_client: Any) -> None:
        store = RedisJobStore(
            url="redis://unused",  # Required param, client is passed directly
            prefix="yomai:test:jobs",
            ttl_secs=3600,
            client=redis_client,
        )
        record = JobRecord(
            id="job-integration-1",
            route="/research",
            stream_url="/__yomai__/jobs/job-integration-1/stream",
            status_url="/__yomai__/jobs/job-integration-1",
        )
        await store.create(record)
        loaded = await store.get("job-integration-1")
        assert loaded is not None
        assert loaded.id == "job-integration-1"
        assert loaded.status == "queued"

    @pytest.mark.asyncio
    async def test_job_lifecycle(self, redis_client: Any) -> None:
        store = RedisJobStore(
            url="redis://unused",
            prefix="yomai:test:jobs",
            ttl_secs=3600,
            client=redis_client,
        )
        record = JobRecord(
            id="job-integration-2",
            route="/research",
            stream_url="/stream",
            status_url="/status",
        )
        await store.create(record)

        # Transition through states
        running = await store.update_status("job-integration-2", "running")
        assert running is not None
        assert running.status == "running"

        done = await store.update_status("job-integration-2", "succeeded", result={"output": "test"})
        assert done is not None
        assert done.status == "succeeded"
        assert done.result == {"output": "test"}

        # Verify final state
        final = await store.get("job-integration-2")
        assert final is not None
        assert final.status == "succeeded"

    @pytest.mark.asyncio
    async def test_list_jobs(self, redis_client: Any) -> None:
        store = RedisJobStore(
            url="redis://unused",
            prefix="yomai:test:jobs",
            ttl_secs=3600,
            client=redis_client,
        )
        # Create multiple jobs
        for i in range(3):
            record = JobRecord(
                id=f"job-list-{i}",
                route="/test",
                stream_url="/stream",
                status_url="/status",
            )
            await store.create(record)

        jobs = list(await store.list())
        assert len(jobs) >= 3


class TestRedisJobEventStoreIntegration:
    """Integration tests for RedisJobEventStore."""

    @pytest.mark.asyncio
    async def test_append_and_read_events(self, redis_client: Any) -> None:
        store = RedisJobEventStore(
            url="redis://unused",
            prefix="yomai:test:events",
            client=redis_client,
        )

        await store.append("job-events-1", "chunk", {"content": "first"})
        await store.append("job-events-1", "chunk", {"content": "second"})

        # Read all events
        events = await store.read_after("job-events-1", "0-0")
        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_event_stream_subscribe(self, redis_client: Any) -> None:
        store = RedisJobEventStore(
            url="redis://unused",
            prefix="yomai:test:events",
            client=redis_client,
        )

        # Add some events
        await store.append("job-stream-1", "result", {"data": "test"})
        await store.append("job-stream-1", "done", {"finished": True})

        # Subscribe from beginning - iterate and break
        event_found = False
        async for event in store.subscribe("job-stream-1", "0-0", heartbeat_secs=0.01):
            if event is not None:
                event_found = True
                break  # Got at least one event

        assert event_found, "Should have received at least one event"

    @pytest.mark.asyncio
    async def test_events_have_ttl(self, redis_client: Any) -> None:
        store = RedisJobEventStore(
            url="redis://unused",
            prefix="yomai:test:events",
            ttl_secs=60,
            client=redis_client,
        )

        await store.append("job-ttl-1", "chunk", {"content": "test"})

        # Verify key exists (TTL would need separate timing test)
        key = "yomai:test:events:jobs:job-ttl-1:events"
        exists = await redis_client.exists(key)
        assert exists == 1


class TestRedisCheckpointStoreIntegration:
    """Integration tests for RedisCheckpointStore."""

    @pytest.mark.asyncio
    async def test_save_and_retrieve_checkpoint(self, redis_client: Any) -> None:
        from yomai.jobs.checkpoints import StepCheckpoint

        store = RedisCheckpointStore(
            url="redis://unused",
            prefix="yomai:test:checkpoints",
            client=redis_client,
        )

        checkpoint = StepCheckpoint(
            job_id="job-checkpoint-1",
            step="analyze",
            input_hash="hash123",
            result="analysis result",
            duration_ms=150,
        )
        await store.save(checkpoint)

        retrieved = await store.get("job-checkpoint-1", "analyze", "hash123")
        assert retrieved is not None
        assert retrieved.job_id == "job-checkpoint-1"
        assert retrieved.step == "analyze"
        assert retrieved.result == "analysis result"

    @pytest.mark.asyncio
    async def test_checkpoint_not_found(self, redis_client: Any) -> None:

        store = RedisCheckpointStore(
            url="redis://unused",
            prefix="yomai:test:checkpoints",
            client=redis_client,
        )

        # Non-existent checkpoint should return None
        result = await store.get("nonexistent", "step", "hash")
        assert result is None


class TestRedisMemoryIntegration:
    """Integration tests for Redis-backed session memory."""

    @pytest.mark.asyncio
    async def test_save_and_load_messages(self, redis_client: Any) -> None:
        memory = RedisMemory(
            url="redis://unused",
            prefix="yomai:test:memory",
            client=redis_client,
        )
        # Use unique session ID to avoid test pollution
        session_id = "session-integration-1"
        # Clear any existing data
        await memory.clear(session_id)

        await memory.save(session_id, "Hello", "Hi there!")

        history = await memory.load(session_id)
        assert len(history) >= 2, f"Expected >= 2 messages, got {len(history)}: {history}"
        assert history[0]["role"] == "user", f"First message role should be user, got: {history[0]}"
        assert history[0]["content"] == "Hello", f"First message content should be Hello, got: {history[0]}"

    @pytest.mark.asyncio
    async def test_max_messages_truncation(self, redis_client: Any) -> None:
        memory = RedisMemory(
            url="redis://unused",
            prefix="yomai:test:memory",
            max_messages=3,
            client=redis_client,
        )

        for i in range(5):
            await memory.save("session-truncation", "user", f"Message {i}")

        history = await memory.load("session-truncation")
        assert len(history) <= 3

    @pytest.mark.asyncio
    async def test_clear_session(self, redis_client: Any) -> None:
        memory = RedisMemory(
            url="redis://unused",
            prefix="yomai:test:memory",
            client=redis_client,
        )

        await memory.save("session-clear", "user", "Hello")
        await memory.clear("session-clear")

        history = await memory.load("session-clear")
        assert len(history) == 0


class TestYomaiAppWithRedisIntegration:
    """Integration tests for Yomai app with Redis backends."""

    @pytest.mark.asyncio
    async def test_app_with_redis_memory(self, redis_client: Any) -> None:
        memory = RedisMemory(
            url="redis://unused",
            prefix="yomai:test:app:memory",
            client=redis_client,
        )

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),  # App config
        )
        # Override with real Redis memory
        app.memory = memory

        @app.agent("/chat")
        async def chat(message: str, session_id: str) -> None:
            pass

        client = YomaiTestClient(app)

        # First message
        with mock_llm(["Hi there!"]):
            result = await client.call("/chat", "Hello", session_id="redis-session-1")
            assert result == "Hi there!"

        # Second message - should have context
        with mock_llm(["How can I help?"]):
            result = await client.call("/chat", "What can you do?", session_id="redis-session-1")

    @pytest.mark.asyncio
    async def test_app_with_redis_jobs(self, redis_client: Any) -> None:
        from yomai.core.app import Yomai

        # Create store with real Redis
        job_store = RedisJobStore(
            url="redis://unused",
            prefix="yomai:test:app:jobs",
            client=redis_client,
        )

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )
        # Override with real Redis job store
        app.jobs = job_store

        @app.workflow("/research", mode="async")
        async def research(topic: str, runner=None) -> dict[str, str]:
            return {"topic": topic, "status": "done"}

        client = YomaiTestClient(app)

        # Create async workflow job
        with mock_llm(["Result"]):
            response = await client.post_json("/research", {"topic": "ai"})

        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Verify job exists in Redis
        job = await job_store.get(job_id)
        assert job is not None


class TestYomaiTestClientIntegration:
    """Extended integration tests for YomaiTestClient with real backends."""

    @pytest.mark.asyncio
    async def test_client_with_tools(self, redis_client: Any) -> None:
        memory = RedisMemory(
            url="redis://unused",
            prefix="yomai:test:client:tools",
            client=redis_client,
        )

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )
        app.memory = memory

        @tool
        async def get_weather(city: str) -> str:
            return f"72°F in {city}"

        @app.agent("/weather-chat", tools=[get_weather])
        async def weather_chat(message: str, session_id: str) -> None:
            pass

        client = YomaiTestClient(app)

        # Test tool calling
        from yomai.testing import MockToolCall
        tool_call = MockToolCall("get_weather", {"city": "Tokyo"})

        with mock_llm([[tool_call], ["Weather retrieved"]]):
            events = await client.get_events("/weather-chat", "What's the weather in Tokyo?", session_id="weather-s1")

        # Verify tool was called
        tool_end_events = [e for e in events if e.get("type") == "tool_end"]
        assert len(tool_end_events) > 0

    @pytest.mark.asyncio
    async def test_client_openapi_schema(self, redis_client: Any) -> None:
        memory = RedisMemory(
            url="redis://unused",
            prefix="yomai:test:client:openapi",
            client=redis_client,
        )

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )
        app.memory = memory

        @app.agent("/chat")
        async def chat(message: str) -> None:
            pass

        @app.workflow("/process")
        async def process(data: str, runner=None) -> str:
            return f"Processed: {data}"

        client = YomaiTestClient(app)

        # Get OpenAPI schema
        async with httpx.AsyncClient(transport=client._transport, base_url="http://testserver") as http_client:
            response = await http_client.get("/__yomai__/openapi.json")

        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "/chat" in schema["paths"]
        assert "/process" in schema["paths"]
