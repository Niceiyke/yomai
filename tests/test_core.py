from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

import httpx
import pytest

from yomai import Yomai, tool
from yomai.config import AgentConfig, LLMConfig, MemoryConfig
from yomai.memory import DictMemory, SqliteMemory
from yomai.streaming.sse import format_sse
from yomai.testing import MockToolCall, YomaiTestClient, capture_tools, mock_llm
from yomai.workflow import WorkflowRunner


def test_sse_format() -> None:
    assert format_sse("done", {"type": "done"}) == 'event: done\ndata: {"type":"done"}\n\n'


def test_tool_schema() -> None:
    @tool
    def add(a: int, b: int = 1) -> int:
        """Add."""
        return a + b

    assert add.schema["properties"]["a"] == {"type": "integer"}
    assert add.schema["required"] == ["a"]


@pytest.mark.asyncio
async def test_memory_truncates() -> None:
    mem = DictMemory(max_messages=3)
    await mem.save("s", "u1", "a1")
    await mem.save("s", "u2", "a2")
    assert len(await mem.load("s")) == 3


@pytest.mark.asyncio
async def test_agent_mock_call_and_memory() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        dev=None,
    )

    @app.agent("/chat")
    async def chat(message: str, session_id: str) -> None:
        pass

    client = YomaiTestClient(app)
    with mock_llm(["Sarah", "Your name is Sarah"]):
        sid = "s1"
        assert await client.call("/chat", "My name is Sarah", session_id=sid) == "Sarah"
        assert await client.call("/chat", "What is my name?", session_id=sid) == "Your name is Sarah"
        history = await app.memory.load(sid)
        assert len(history) == 4


@pytest.mark.asyncio
async def test_tool_capture() -> None:
    @tool
    async def get_weather(city: str) -> str:
        return f"real {city}"

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.agent("/chat", tools=[get_weather])
    async def chat(message: str, session_id: str) -> None:
        pass

    tool_call = MockToolCall("get_weather", {"city": "Tokyo"})
    with mock_llm([[tool_call], ["sunny"]]), capture_tools("72F") as calls:
        events = await YomaiTestClient(app).get_events("/chat", "weather")
    assert calls[0].name == "get_weather"
    assert calls[0].args == {"city": "Tokyo"}
    assert any(event.get("type") == "tool_end" for event in events)


@pytest.mark.asyncio
async def test_workflow_result() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.workflow("/research")
    async def research(topic: str, runner: WorkflowRunner) -> dict[str, str]:
        return {"topic": topic}

    events = await YomaiTestClient(app).get_events("/research", "ignored", extra_body={"topic": "ai"})
    assert any(event.get("type") == "result" for event in events)


@pytest.mark.asyncio
async def test_route_metadata_params() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.agent("/chat")
    async def chat(message: str, session_id: str) -> None:
        pass

    @app.workflow("/research")
    async def research(topic: str, depth: int = 1, runner: WorkflowRunner | None = None) -> str:
        return topic

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__yomai__/routes")
    routes = response.json()
    chat_meta = next(route for route in routes if route["path"] == "/chat")
    workflow_meta = next(route for route in routes if route["path"] == "/research")
    assert chat_meta["body_params"] == ["message"]
    assert chat_meta["injected_params"] == ["session_id"]
    assert [param["name"] for param in workflow_meta["params"]] == ["topic", "depth"]
    assert workflow_meta["body_params"] == ["topic", "depth"]


@pytest.mark.asyncio
async def test_playground_production_404() -> None:
    old = os.environ.get("YOMAI_ENV")
    os.environ["YOMAI_ENV"] = "production"
    try:
        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )
        transport = httpx.ASGITransport(app=cast(Any, app))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/__yomai__")
        assert response.status_code == 404
    finally:
        if old is None:
            os.environ.pop("YOMAI_ENV", None)
        else:
            os.environ["YOMAI_ENV"] = old


@pytest.mark.asyncio
async def test_timeout_secs_must_be_positive() -> None:
    """timeout_secs=0 (or negative) is rejected by config validation."""
    from yomai.exceptions import YomaiConfigError

    with pytest.raises(YomaiConfigError, match="timeout_secs must be positive"):
        AgentConfig(timeout_secs=0)

    with pytest.raises(YomaiConfigError, match="timeout_secs must be positive"):
        AgentConfig(timeout_secs=-1)


@pytest.mark.asyncio
async def test_tool_call_streams_before_provider_done() -> None:
    from yomai.core.agent import AgentLoop
    from yomai.llm.base import Done, LLMEvent, Message, ToolCall, ToolSchema

    @tool
    def instant() -> str:
        return "ok"

    class SlowDoneProvider:
        async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
            yield ToolCall(id="t1", name="instant", args={})
            await asyncio.sleep(0.05)
            yield Done(1, 1)

    loop = AgentLoop(cast(Any, SlowDoneProvider()), [instant], AgentConfig(), LLMConfig(api_key="x"))
    stream_gen: AsyncGenerator[str, None] = loop.run("run tool", [], "")
    # Skip past graph events (user_msg, llm_0, edges) to reach tool_start
    first: str = ""
    async for sse in stream_gen:
        if sse.startswith("event: tool_start"):
            first = sse
            break
    assert first.startswith("event: tool_start")
    await stream_gen.aclose()


@pytest.mark.asyncio
async def test_strip_reasoning() -> None:
    from yomai.core.agent import AgentLoop
    from yomai.llm.base import Done, LLMEvent, Message, TextChunk, ToolSchema

    REASONING_OPEN = "<think>"
    REASONING_CLOSE = "</think>"

    class ReasoningProvider:
        async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
            yield TextChunk(REASONING_OPEN + "reasoning..." + REASONING_CLOSE + "\n")
            yield TextChunk("hello world")
            yield TextChunk(REASONING_OPEN + "done" + REASONING_CLOSE + "\n")
            yield Done(1, 1)

    loop = AgentLoop(
        cast(Any, ReasoningProvider()), [], AgentConfig(), LLMConfig(api_key="x", strip_reasoning=True)
    )
    chunks: list[str] = []
    async for sse in loop.run("hi", [], ""):
        chunks.append(sse)
    joined = "".join(chunks)
    assert REASONING_OPEN not in joined
    assert "hello world" in joined


def test_memory_config_guard_sqlite() -> None:
    cfg = MemoryConfig(backend="sqlite", db_path="/tmp/yomai_test.db")
    assert cfg.backend == "sqlite"
    assert cfg.db_path == "/tmp/yomai_test.db"


@pytest.mark.asyncio
async def test_sqlite_memory_persists_across_instances() -> None:
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    try:
        mem1 = SqliteMemory(db_path=db, max_messages=5)
        await mem1.save("s", "hello", "hi")
        mem2 = SqliteMemory(db_path=db, max_messages=5)
        h = await mem2.load("s")
        assert len(h) == 2
        assert h[-1]["content"] == "hi"
    finally:
        if os.path.exists(db):
            os.unlink(db)

@pytest.mark.asyncio
async def test_openapi_schema_generated() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))
    @app.agent("/chat")
    async def chat(message: str) -> None:
        pass
    @app.workflow("/research")
    async def research(topic: str, runner=None) -> str:
        return topic
    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/__yomai__/openapi.json")
    schema = resp.json()
    assert schema["openapi"] == "3.1.0"
    assert "/chat" in schema["paths"]
    assert schema["paths"]["/chat"]["post"]["x-yomai-type"] == "agent"
    assert "/research" in schema["paths"]
    assert schema["paths"]["/research"]["post"]["x-yomai-type"] == "workflow"
    assert "components" in schema
    assert "securitySchemes" in schema["components"]


@pytest.mark.asyncio
async def test_openapi_schema_security_when_api_key_set() -> None:
    from yomai.config import DevConfig
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        dev=DevConfig(api_key="my-key"),
    )
    @app.agent("/secure")
    async def chat(msg: str) -> None:
        pass
    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/__yomai__/openapi.json")
    schema = resp.json()
    post = schema["paths"]["/secure"]["post"]
    assert post["security"] == [{"ApiKeyAuth": []}]


@pytest.mark.asyncio
async def test_api_key_auth_on_agent() -> None:
    from yomai.config import DevConfig
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        dev=DevConfig(api_key="secret"),
    )
    @app.agent("/auth-chat")
    async def chat(message: str) -> None:
        pass
    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/auth-chat", json={"message": "hi"})
        assert r1.status_code == 401
        r2 = await client.post("/auth-chat", json={"message": "hi"}, headers={"Authorization": "Bearer secret"})
        assert r2.status_code != 401


@pytest.mark.asyncio
async def test_agent_handler_receives_extra_body_and_session() -> None:
    seen: dict[str, Any] = {}
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/chat")
    async def chat(message: str, session_id: str, tone: str = "plain") -> None:
        seen.update({"message": message, "session_id": session_id, "tone": tone})

    with mock_llm(["ok"]):
        assert await YomaiTestClient(app).call("/chat", "hello", session_id="sid", extra_body={"tone": "warm"}) == "ok"
    assert seen == {"message": "hello", "session_id": "sid", "tone": "warm"}


@pytest.mark.asyncio
async def test_route_tool_isolation() -> None:
    @tool
    def secret_tool() -> str:
        return "secret leaked"

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/chat", tools=[])
    async def chat(message: str) -> None:
        pass

    with mock_llm([[MockToolCall("secret_tool", {})]]):
        events = await YomaiTestClient(app).get_events("/chat", "try tool")
    assert any(event.get("code") == "unknown_tool" for event in events)
    assert not any(event.get("result") == "secret leaked" for event in events)


@pytest.mark.asyncio
async def test_metadata_requires_auth_in_production() -> None:
    from yomai.config import DevConfig

    old = os.environ.get("YOMAI_ENV")
    os.environ["YOMAI_ENV"] = "production"
    try:
        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
            dev=DevConfig(api_key="meta-secret"),
        )
        transport = httpx.ASGITransport(app=cast(Any, app))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/__yomai__/routes")).status_code == 401
            ok = await client.get("/__yomai__/routes", headers={"Authorization": "Bearer meta-secret"})
            assert ok.status_code == 200
    finally:
        if old is None:
            os.environ.pop("YOMAI_ENV", None)
        else:
            os.environ["YOMAI_ENV"] = old


@pytest.mark.asyncio
async def test_streaming_errors_are_generic_in_production() -> None:
    old = os.environ.get("YOMAI_ENV")
    os.environ["YOMAI_ENV"] = "production"
    try:
        app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

        @app.agent("/boom")
        async def boom(message: str) -> None:
            raise RuntimeError("sensitive detail")

        events = await YomaiTestClient(app).get_events("/boom", "hi")
        error = next(event for event in events if event.get("type") == "error")
        assert error["message"] == "Internal server error"
        assert "sensitive" not in str(events)
    finally:
        if old is None:
            os.environ.pop("YOMAI_ENV", None)
        else:
            os.environ["YOMAI_ENV"] = old


def test_openai_provider_defaults_are_provider_specific() -> None:
    cfg = LLMConfig(provider="openai", api_key="x")
    assert cfg.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_agent_extra_body_is_pydantic_validated() -> None:
    seen: dict[str, Any] = {}
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/typed")
    async def typed(message: str, count: int) -> None:
        seen["count"] = count

    with mock_llm(["ok"]):
        await YomaiTestClient(app).call("/typed", "hi", extra_body={"count": "3"})
    assert seen["count"] == 3
    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/typed", json={"message": "hi", "count": "bad"})
    assert response.status_code == 400
    assert "Invalid field count" in response.json()["error"]


@pytest.mark.asyncio
async def test_workflow_body_is_pydantic_validated() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/typed-workflow")
    async def typed_workflow(count: int) -> int:
        return count + 1

    events = await YomaiTestClient(app).get_events("/typed-workflow", "ignored", extra_body={"count": "2"})
    assert any(event.get("type") == "result" and event.get("content") == "3" for event in events)
    bad = await YomaiTestClient(app).get_events("/typed-workflow", "ignored", extra_body={"count": "bad"})
    assert any(event.get("type") == "error" and "Invalid field count" in event.get("message", "") for event in bad)


@pytest.mark.asyncio
async def test_per_route_api_key_override() -> None:
    from yomai.config import DevConfig

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"), dev=DevConfig(api_key="global"))

    @app.agent("/public", api_key="")
    async def public(message: str) -> None:
        pass

    @app.workflow("/route-secret", api_key="route")
    async def route_secret() -> str:
        return "ok"

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/public", json={"message": "hi"})).status_code != 401
        assert (await client.post("/route-secret", json={})).status_code == 401
        assert (await client.post("/route-secret", json={}, headers={"Authorization": "Bearer route"})).status_code != 401


def test_signed_session_middleware_sign_and_verify() -> None:
    from yomai.middleware import SignedSessionMiddleware

    mw = SignedSessionMiddleware(lambda scope, receive, send: None, secret="secret")
    signed = mw.sign("session-1")
    assert mw.verify(signed) == "session-1"
    assert mw.verify(signed + "tampered") is None


@pytest.mark.asyncio
async def test_openapi_includes_tool_schemas() -> None:
    @tool
    def lookup(city: str) -> str:
        """Lookup a city."""
        return city

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/with-tool", tools=[lookup])
    async def chat(message: str) -> None:
        pass

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        schema = (await client.get("/__yomai__/openapi.json")).json()
    assert schema["components"]["schemas"]["Tool_lookup"]["properties"]["city"] == {"type": "string"}


@pytest.mark.asyncio
async def test_dict_memory_ttl_evicts_expired_session() -> None:
    mem = DictMemory(max_messages=5, ttl_hours=1)
    await mem.save("s", "hello", "hi")
    mem._store["s"] = (asyncio.get_running_loop().time() - 7200, mem._store["s"][1])
    assert await mem.load("s") == []


@pytest.mark.asyncio
async def test_sqlite_memory_ttl_evicts_expired_session() -> None:
    import sqlite3
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    try:
        mem = SqliteMemory(db_path=db, max_messages=5, ttl_hours=1)
        await mem.save("s", "hello", "hi")
        conn = sqlite3.connect(db)
        conn.execute("UPDATE sessions SET updated_at = strftime('%s','now') - 7200 WHERE session_id = 's'")
        conn.commit()
        conn.close()
        assert await mem.load("s") == []
    finally:
        if os.path.exists(db):
            os.unlink(db)


# ---------------------------------------------------------------------------
# SSE sanitization tests
# ---------------------------------------------------------------------------


def test_format_sse_sanitizes_newlines_in_data() -> None:
    """SSE data values containing newlines are replaced with spaces."""
    result = format_sse("chunk", {"content": "hello\n\nworld"})
    # The literal "\n\n" from the original data must not leak into the SSE output
    assert "hello\n\nworld" not in result
    assert "hello  world" in result


def test_format_sse_sanitizes_newlines_in_event_type() -> None:
    """SSE event type containing newlines is cleaned."""
    result = format_sse("bad\nevent", {"type": "x"})
    assert "bad\nevent" not in result
    assert "badevent" in result


def test_format_sse_sanitizes_nested_newlines() -> None:
    """Nested dict and list values with newlines are sanitized."""
    data = {
        "items": ["a\nb", "c\nd"],
        "meta": {"note": "line1\nline2"},
    }
    result = format_sse("chunk", data)
    assert "a\nb" not in result
    assert "c\nd" not in result
    assert "line1\nline2" not in result
    assert "a b" in result
    assert "c d" in result
    assert "line1 line2" in result


# ---------------------------------------------------------------------------
# strip_reasoning tests
# ---------------------------------------------------------------------------


def test_strip_reasoning_removes_think_blocks() -> None:
    """Content inside <think>...</think> tags is removed when strip_reasoning is True."""
    from unittest.mock import MagicMock

    from yomai.core.agent import AgentLoop

    mock_provider = MagicMock()
    loop = AgentLoop(mock_provider, [], AgentConfig(), LLMConfig(api_key="x", strip_reasoning=True))
    result = loop._maybe_strip_reasoning("<think>foo</think>bar")
    assert result == "bar"


def test_strip_reasoning_preserves_without_flag() -> None:
    """Content is unchanged when strip_reasoning is False."""
    from unittest.mock import MagicMock

    from yomai.core.agent import AgentLoop

    mock_provider = MagicMock()
    loop = AgentLoop(mock_provider, [], AgentConfig(), LLMConfig(api_key="x", strip_reasoning=False))
    original = "<think>foo</think>bar"
    result = loop._maybe_strip_reasoning(original)
    assert result == original


def test_strip_reasoning_handles_split_blocks() -> None:
    """Reasoning blocks split across multiple chunks are handled correctly via _inside_reasoning."""
    from unittest.mock import MagicMock

    from yomai.core.agent import AgentLoop

    mock_provider = MagicMock()
    loop = AgentLoop(mock_provider, [], AgentConfig(), LLMConfig(api_key="x", strip_reasoning=True))

    # First chunk opens a think tag but does not close it
    result1 = loop._maybe_strip_reasoning("<think>part1")
    assert result1 == ""
    assert loop._inside_reasoning is True

    # Second chunk continues the reasoning and closes the tag
    result2 = loop._maybe_strip_reasoning("part2</think>after")
    assert result2 == "after"
    assert loop._inside_reasoning is False

