from __future__ import annotations

from typing import Any, cast

import pytest

from yomai import Yomai, tool
from yomai.config import LLMConfig, MemoryConfig
from yomai.testing import YomaiTestClient, mock_llm
from yomai.workflow import WorkflowRunner


# -------------------------------------------------------------------
# Shared state
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_state_accumulates_step_outputs() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/step1")
    async def step1(message: str) -> None: pass

    @app.agent("/step2")
    async def step2(message: str) -> None: pass

    @app.workflow("/stateful")
    async def stateful(runner: WorkflowRunner):
        with mock_llm(["alpha"]):
            r1 = await runner.step("first", step1, "go")
        assert runner.state["first"] == "alpha"
        assert r1 == "alpha"

        with mock_llm(["beta"]):
            r2 = await runner.step("second", step2, runner.state["first"])
        assert runner.state["second"] == "beta"
        assert r2 == "beta"

        return runner.state

    events = await YomaiTestClient(app).get_events("/stateful", "ignored")
    result = next(e for e in events if e.get("type") == "result")
    import json
    state = json.loads(result["content"])
    assert state == {"first": "alpha", "second": "beta"}


# -------------------------------------------------------------------
# Retry on step failure
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_retry_succeeds_on_second_attempt() -> None:
    from yomai.llm.base import Done, LLMEvent, Message, ToolSchema
    from collections.abc import AsyncIterator

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    call_count = 0

    class FailingThenOkProvider:
        async def stream(self, messages: list[Message], tools: list[ToolSchema],
                         system: str) -> AsyncIterator[LLMEvent]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("transient api error")
            yield Done(1, 1)

        def tool_schemas(self, tools): return []
        def tool_result_messages(self, tc, r): return []

    # Replace provider factory to return our flaky provider
    original_build = app._build_provider
    app._build_provider = lambda: cast(Any, FailingThenOkProvider())

    try:
        @app.agent("/flaky")
        async def flaky(message: str) -> None: pass

        @app.workflow("/retry-wf")
        async def retry_wf(runner: WorkflowRunner):
            return await runner.step("flaky-step", flaky, "go", retries=2, backoff_secs=0.01)

        events = await YomaiTestClient(app).get_events("/retry-wf", "ignored")
        assert call_count == 2
        assert any(e.get("type") == "result" for e in events)
    finally:
        app._build_provider = original_build


@pytest.mark.asyncio
async def test_step_retry_exhausted_raises() -> None:
    from yomai.llm.base import LLMEvent, Message, ToolSchema
    from collections.abc import AsyncIterator

    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    class AlwaysFailsProvider:
        async def stream(self, messages: list[Message], tools: list[ToolSchema],
                         system: str) -> AsyncIterator[LLMEvent]:
            raise RuntimeError("permanent failure")

        def tool_schemas(self, tools): return []
        def tool_result_messages(self, tc, r): return []

    original_build = app._build_provider
    app._build_provider = lambda: cast(Any, AlwaysFailsProvider())

    try:
        @app.agent("/always-fails")
        async def always_fails(message: str) -> None: pass

        @app.workflow("/exhausted")
        async def exhausted(runner: WorkflowRunner):
            return await runner.step("bad", always_fails, "go", retries=1, backoff_secs=0.01)

        events = await YomaiTestClient(app).get_events("/exhausted", "ignored")
        assert any(e.get("type") == "error" for e in events)
    finally:
        app._build_provider = original_build


# -------------------------------------------------------------------
# Direct tool execution
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_tool_calls_directly_without_llm() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @tool
    def double(x: int) -> int:
        return x * 2

    @app.agent("/noop")
    async def noop(message: str) -> None: pass

    @app.workflow("/tool-wf")
    async def tool_wf(runner: WorkflowRunner):
        val = await runner.tool(double, x=21)
        assert val == 42

        with mock_llm(["got it"]):
            return await runner.step("verify", noop, str(val))

    events = await YomaiTestClient(app).get_events("/tool-wf", "ignored")
    assert any(e.get("type") == "result" for e in events)


# -------------------------------------------------------------------
# Branching
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_branch_takes_true_path() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/t")
    async def t(message: str) -> None: pass

    @app.agent("/f")
    async def f(message: str) -> None: pass

    @app.workflow("/branch-wf")
    async def branch_wf(topic: str, runner: WorkflowRunner):
        runner.state["topic"] = topic
        return await runner.branch(
            "quality",
            condition=lambda s: len(s["topic"]) > 3,
            on_true=lambda: runner.step("good", t, "good path"),
            on_false=lambda: runner.step("bad", f, "bad path"),
        )

    with mock_llm(["true-path"]):
        events = await YomaiTestClient(app).get_events("/branch-wf", "test-topic",
            extra_body={"topic": "ai-ethics"})

    result = next(e for e in events if e.get("type") == "result")
    assert "true-path" in str(result.get("content", ""))


@pytest.mark.asyncio
async def test_branch_takes_false_path() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/t2")
    async def t2(message: str) -> None: pass

    @app.agent("/f2")
    async def f2(message: str) -> None: pass

    @app.workflow("/branch-false")
    async def branch_false(topic: str, runner: WorkflowRunner):
        runner.state["topic"] = topic
        return await runner.branch(
            "quality",
            condition=lambda s: len(s["topic"]) > 10,
            on_true=lambda: runner.step("long", t2, "long"),
            on_false=lambda: runner.step("short", f2, "short"),
        )

    with mock_llm(["false-path"]):
        events = await YomaiTestClient(app).get_events("/branch-false", "test-topic",
            extra_body={"topic": "hi"})

    result = next(e for e in events if e.get("type") == "result")
    assert "false-path" in str(result.get("content", ""))


# -------------------------------------------------------------------
# Agent delegation
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delegate_runs_sub_agent() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/specialist")
    async def specialist(message: str) -> None: pass

    @app.workflow("/orchestrator")
    async def orchestrator(runner: WorkflowRunner):
        with mock_llm(["specialist says hi"]):
            answer = await runner.delegate(specialist, "help me")
        return answer

    events = await YomaiTestClient(app).get_events("/orchestrator", "ignored")
    result = next(e for e in events if e.get("type") == "result")
    assert "specialist says hi" in str(result.get("content", ""))


@pytest.mark.asyncio
async def test_delegate_stores_result_in_state() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/helper")
    async def helper(message: str) -> None: pass

    @app.workflow("/del-state")
    async def del_state(runner: WorkflowRunner):
        with mock_llm(["done"]):
            await runner.delegate(helper, "do it")
        assert runner.state["helper"] == "done"
        return runner.state["helper"]

    events = await YomaiTestClient(app).get_events("/del-state", "ignored")
    result = next(e for e in events if e.get("type") == "result")
    assert "done" in str(result.get("content", ""))


# -------------------------------------------------------------------
# Graph events for new features
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_emits_graph_events() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @tool
    def lookup(query: str) -> str:
        return f"found {query}"

    @app.workflow("/graph-tool")
    async def graph_tool(runner: WorkflowRunner):
        return await runner.tool(lookup, query="test")

    events = await YomaiTestClient(app).get_events("/graph-tool", "ignored")
    graph_events = [e for e in events if e.get("event") == "graph"]
    kinds = [e.get("kind") for e in graph_events if "kind" in e]
    assert "tool" in kinds


@pytest.mark.asyncio
async def test_branch_emits_graph_events() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/x")
    async def x(message: str) -> None: pass

    @app.workflow("/graph-branch")
    async def graph_branch(runner: WorkflowRunner):
        with mock_llm(["ok"]):
            return await runner.branch(
                "check",
                condition=lambda s: True,
                on_true=lambda: runner.step("yes", x, "y"),
                on_false=lambda: runner.step("no", x, "n"),
            )

    events = await YomaiTestClient(app).get_events("/graph-branch", "ignored")
    graph_events = [e for e in events if e.get("event") == "graph"]
    kinds = [e.get("kind") for e in graph_events if "kind" in e]
    # Should have branch node and step node
    assert "parallel" in kinds  # branch nodes use "parallel" kind
    assert "step" in kinds


@pytest.mark.asyncio
async def test_delegate_emits_graph_events() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/sub")
    async def sub(message: str) -> None: pass

    @app.workflow("/graph-del")
    async def graph_del(runner: WorkflowRunner):
        with mock_llm(["sub result"]):
            return await runner.delegate(sub, "hello")

    events = await YomaiTestClient(app).get_events("/graph-del", "ignored")
    graph_events = [e for e in events if e.get("event") == "graph"]
    kinds = [e.get("kind") for e in graph_events if "kind" in e]
    assert "step" in kinds  # delegate creates a step node
    # Should contain delegate label
    labels = [e.get("label", "") for e in graph_events if "label" in e]
    assert any("delegate" in lbl for lbl in labels)
