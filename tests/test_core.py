from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast

import httpx
import pytest

from yomai import Yomai, tool
from yomai.config import AgentConfig, LLMConfig, MemoryConfig
from yomai.core.agent import AgentLoop
from yomai.llm import Done, LLMEvent, LLMProvider, Message, ToolCall, ToolSchema
from yomai.memory import DictMemory
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

    assert add.schema["properties"]["a"] == {"type": "integer"}  # type: ignore[attr-defined]
    assert add.schema["required"] == ["a"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_memory_truncates() -> None:
    mem = DictMemory(max_messages=3)
    await mem.save("s", "u1", "a1")
    await mem.save("s", "u2", "a2")
    assert len(await mem.load("s")) == 3


@pytest.mark.asyncio
async def test_agent_mock_call_and_memory() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), dev=None)

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

    app = Yomai(llm=LLMConfig(api_key=""))

    @app.agent("/chat", tools=[get_weather])
    async def chat(message: str, session_id: str) -> None:
        pass

    with mock_llm([[MockToolCall("get_weather", {"city": "Tokyo"})], ["sunny"]]):
        with capture_tools("72F") as calls:
            events = await YomaiTestClient(app).get_events("/chat", "weather")
    assert calls[0].name == "get_weather"
    assert calls[0].args == {"city": "Tokyo"}
    assert any(event.get("type") == "tool_end" for event in events)


@pytest.mark.asyncio
async def test_workflow_result() -> None:
    app = Yomai(llm=LLMConfig(api_key=""))

    @app.workflow("/research")
    async def research(topic: str, runner: WorkflowRunner) -> dict[str, str]:
        return {"topic": topic}

    events = await YomaiTestClient(app).get_events("/research", "ignored", extra_body={"topic": "ai"})
    assert any(event.get("type") == "result" for event in events)


@pytest.mark.asyncio
async def test_route_metadata_params() -> None:
    app = Yomai(llm=LLMConfig(api_key=""))

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
        app = Yomai(llm=LLMConfig(api_key=""))
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
async def test_timeout_does_not_save_memory() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), agent=AgentConfig(timeout_secs=0))

    @app.agent("/chat")
    async def chat(message: str, session_id: str) -> None:
        pass

    with mock_llm(["late"]):
        events = await YomaiTestClient(app).get_events("/chat", "hello", session_id="timeout")
    assert any(event.get("code") == "timeout" for event in events)
    assert await app.memory.load("timeout") == []


@pytest.mark.asyncio
async def test_tool_call_streams_before_provider_done() -> None:
    @tool
    def instant() -> str:
        return "ok"

    class SlowDoneProvider(LLMProvider):
        async def stream(self, messages: list[Message], tools: list[ToolSchema], system: str) -> AsyncIterator[LLMEvent]:
            yield ToolCall(id="t1", name="instant", args={})
            await asyncio.sleep(0.05)
            yield Done(1, 1)

    loop = AgentLoop(SlowDoneProvider(), [instant], AgentConfig(), LLMConfig(api_key="x"))
    stream: AsyncGenerator[str, None] = loop.run("run tool", [], "")
    first = await anext(stream)
    assert first.startswith("event: tool_start")
    await stream.aclose()


def test_memory_config_guard() -> None:
    with pytest.raises(Exception):
        MemoryConfig(backend="redis")
