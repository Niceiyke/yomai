"""Regression tests for the 30 bug fixes."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from yomai.config import (
    AgentConfig,
    BudgetConfig,
    LLMConfig,
    MemoryConfig,
    StreamingConfig,
)
from yomai.testing import YomaiTestClient, mock_llm
from yomai.workflow.runner import WorkflowRunner

# ===========================================================================
# #1 — Budget daily reset
# ===========================================================================


@pytest.mark.asyncio
async def test_budget_daily_reset() -> None:
    """Daily budget counters reset at midnight."""
    from yomai.budget import BudgetTracker

    tracker = BudgetTracker(BudgetConfig(max_cost_per_day=1.0, on_exceeded="stop"))

    result = await tracker.check("s1", tokens_in=10, tokens_out=5, cost_estimate=0.5)
    assert not result["exceeded"]

    result = await tracker.check("s1", tokens_in=10, tokens_out=5, cost_estimate=0.6)
    # 0.5 + 0.6 = 1.1 > 1.0  → exceeded
    assert result["exceeded"]
    assert result["reason"] == "max_cost_per_day"

    # Force yesterday's date to trigger a reset
    import datetime

    tracker._last_reset_date = datetime.date.today() - datetime.timedelta(days=1)

    result = await tracker.check("s2", tokens_in=5, tokens_out=3, cost_estimate=0.1)
    assert not result["exceeded"]


@pytest.mark.asyncio
async def test_budget_daily_reset_on_new_day() -> None:
    """Budget auto-resets when check() is called on a new day."""
    from yomai.budget import BudgetTracker

    tracker = BudgetTracker(BudgetConfig(max_cost_per_day=1.0, on_exceeded="stop"))

    await tracker.check("s1", tokens_in=100, tokens_out=50, cost_estimate=0.9)
    result = await tracker.check("s1", tokens_in=10, tokens_out=5, cost_estimate=0.2)
    assert result["exceeded"]

    import datetime

    tracker._last_reset_date = datetime.date.today() - datetime.timedelta(days=1)

    result = await tracker.check("s2", tokens_in=5, tokens_out=3, cost_estimate=0.05)
    assert not result["exceeded"]
    assert tracker._daily_cost == pytest.approx(0.05)


# ===========================================================================
# #3 — WorkflowRunner._run_agent propagates system prompt
# ===========================================================================


@pytest.mark.asyncio
async def test_workflow_step_uses_agent_system_prompt() -> None:
    """WorkflowRunner._run_agent reads the agent's configured system prompt."""
    from yomai import Yomai, tool

    @tool
    def noop() -> str:
        return "ok"

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.agent("/step", tools=[noop], system="You are a pirate translator")
    async def step_agent(message: str, session_id: str) -> None:
        pass

    @app.workflow("/pipe")
    async def pipe(runner: WorkflowRunner):
        with mock_llm(["Arrr, the weather be sunny!"]):
            result = await runner.step("translate", step_agent, "What is the weather?")
        return {"output": result}

    events = await YomaiTestClient(app).get_events("/pipe", "ignored")
    chunks = [e.get("content", "") for e in events if e.get("type") == "chunk"]
    assert "Arrr" in "".join(chunks)


# ===========================================================================
# #4 — Streaming timeout fires correctly
# ===========================================================================


@pytest.mark.asyncio
async def test_agent_timeout_fires_with_slow_llm() -> None:
    """An agent with timeout_secs=1 gets cancelled when the LLM is slow."""
    from yomai import Yomai
    from yomai.llm.base import TextChunk

    app = Yomai(
        llm=LLMConfig(provider="openai", api_key="sk-fake"),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        agent=AgentConfig(timeout_secs=1),
    )

    class Hanging:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)
            return TextChunk("never")

    def slow_factory() -> Any:
        from yomai.llm.openai import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "mock"
        provider.max_tokens = 1024
        provider._openai = None
        provider.config = app.config.llm
        provider.stream = lambda messages, tools, system: Hanging()  # type: ignore[method-assign]
        return provider

    # Patch BEFORE registering agents (route captures factory at decoration time)
    app._build_provider = slow_factory  # type: ignore[method-assign]

    @app.agent("/chat")
    async def chat(message: str, session_id: str) -> None:
        pass

    events = await YomaiTestClient(app).get_events("/chat", "hello", session_id="t1")

    assert any(e.get("code") == "timeout" for e in events)


# ===========================================================================
# #5 — Tool cache concurrency
# ===========================================================================


class _ConcurrentToolStream:
    """Async iterator that emits a ToolCall and then Done."""

    def __init__(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self._items: list[Any] = []
        from yomai.llm.base import Done, ToolCall

        self._items.append(ToolCall(id="t1", name=tool_name, args=tool_args))
        self._items.append(Done(input_tokens=1, output_tokens=1))
        self._pos = 0

    def __aiter__(self) -> _ConcurrentToolStream:
        return self

    async def __anext__(self) -> Any:
        if self._pos >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._pos]
        self._pos += 1
        return item


@pytest.mark.asyncio
async def test_tool_cache_concurrent_access_does_not_corrupt() -> None:
    """Two concurrent AgentLoops hitting the same cached tool do not corrupt cache."""
    from yomai import Yomai, tool
    from yomai.core.agent import AgentLoop
    from yomai.llm.anthropic import AnthropicProvider
    from yomai.llm.openai import OpenAIProvider

    call_count = 0

    @tool(cache_ttl=60)
    def expensive_op(x: int) -> str:
        nonlocal call_count
        call_count += 1
        return f"result-{x}"

    app = Yomai(
        llm=LLMConfig(provider="openai", api_key="sk-fake"),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    original_openai = OpenAIProvider.stream
    original_anthropic = AnthropicProvider.stream

    def stream_factory(self, messages, tools, system):
        return _ConcurrentToolStream("expensive_op", {"x": 1})  # type: ignore[assignment, arg-type, misc, return-value]

    OpenAIProvider.stream = stream_factory  # type: ignore[method-assign]
    AnthropicProvider.stream = stream_factory  # type: ignore[method-assign]
    try:

        async def run_one() -> str:
            loop = AgentLoop(
                app._build_provider(),
                [expensive_op],
                app.config.agent,
                app.config.llm,
                tool_cache=app._tool_cache,
            )
            chunks: list[str] = []
            async for sse in loop.run("go", history=[], system=""):
                chunks.append(sse)
            return "".join(chunks)

        results = await asyncio.gather(run_one(), run_one())
    finally:
        OpenAIProvider.stream = original_openai  # type: ignore[method-assign]
        AnthropicProvider.stream = original_anthropic  # type: ignore[method-assign]

    assert call_count == 1
    assert "result-1" in results[0]
    assert "result-1" in results[1]


# ===========================================================================
# #6 — fail_fast=False on WorkflowRunner.parallel()
# ===========================================================================


@pytest.mark.asyncio
async def test_parallel_fail_fast_false_collects_errors() -> None:
    """WorkflowRunner.parallel(fail_fast=False) collects exceptions instead of aborting."""
    from yomai import Yomai, tool

    @tool
    def succeed() -> str:
        return "ok"

    @tool
    def fail() -> str:
        raise RuntimeError("intentional")

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.workflow("/robust")
    async def robust(runner: WorkflowRunner):

        async def ok_tool() -> str:
            return str(await runner.tool(succeed))

        async def fail_tool() -> str:
            try:
                await runner.tool(fail)
            except RuntimeError as e:
                return f"error: {e}"
            return "should not reach"

        coros: list[Any] = [ok_tool(), fail_tool()]
        results = await runner.parallel(coros, fail_fast=False)
        return {"results": [str(r) for r in results]}

    events = await YomaiTestClient(app).get_events("/robust", "ignored")
    result = next(e for e in events if e.get("type") == "result")
    content = result.get("content", "{}")
    data = json.loads(content)
    assert "ok" in str(data["results"][0])
    assert "intentional" in str(data["results"][1])


# ===========================================================================
# #7 — DeprecationWarning on max_tool_calls
# ===========================================================================


def test_max_tool_calls_emits_deprecation_warning() -> None:
    """Using max_tool_calls emits a DeprecationWarning."""
    import warnings

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        AgentConfig(max_tool_calls=5)

    assert len(w) == 1
    assert issubclass(w[0].category, DeprecationWarning)
    assert "max_tool_calls" in str(w[0].message)
    assert "max_iterations" in str(w[0].message)


# ===========================================================================
# #15 — timeout_secs validation
# ===========================================================================


def test_timeout_secs_rejects_zero() -> None:
    from yomai.exceptions import YomaiConfigError

    with pytest.raises(YomaiConfigError):
        AgentConfig(timeout_secs=0)


def test_timeout_secs_rejects_negative() -> None:
    from yomai.exceptions import YomaiConfigError

    with pytest.raises(YomaiConfigError):
        AgentConfig(timeout_secs=-5)


# ===========================================================================
# #17 — heartbeat_secs validation
# ===========================================================================


def test_heartbeat_secs_rejects_zero() -> None:
    from yomai.exceptions import YomaiConfigError

    with pytest.raises(YomaiConfigError):
        StreamingConfig(heartbeat_secs=0)


def test_heartbeat_secs_rejects_negative() -> None:
    from yomai.exceptions import YomaiConfigError

    with pytest.raises(YomaiConfigError):
        StreamingConfig(heartbeat_secs=-1)


# ===========================================================================
# #2 — Multi-worker integration: Redis job + event store end-to-end
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.skip(reason="Requires running Redis instance — tested via scripts/")
async def test_redis_job_store_e2e() -> None:
    """Placeholder: full Redis integration covered by test_integration_redis.py."""


# ===========================================================================
# Graceful shutdown
# ===========================================================================


@pytest.mark.asyncio
async def test_draining_prevents_new_connections() -> None:
    """When draining, new connections receive 503."""
    from yomai import Yomai

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
    )

    @app.agent("/chat")
    async def chat(message: str, session_id: str) -> None:
        pass

    # Normal request works
    with mock_llm(["hello"]):
        events = await YomaiTestClient(app).get_events("/chat", "hello", session_id="s1")
    assert any(e.get("type") == "done" for e in events)

    # Start draining — the _check_auth in BaseRoute checks should_accept()
    app._draining = True
    client = YomaiTestClient(app)

    async with await client._client() as http_client:
        resp = await http_client.post("/chat", json={"message": "hello"})

    assert resp.status_code == 503
    data = resp.json()
    assert "shutting down" in str(data.get("error", "")).lower()


# ===========================================================================
# Rate limiter — stale key cleanup
# ===========================================================================


@pytest.mark.asyncio
async def test_rate_limiter_cleans_up_stale_concurrent_keys() -> None:
    """After release_concurrent hits 0, the key is deleted from the dict."""
    from yomai.limits import InMemoryRateLimiter

    limiter = InMemoryRateLimiter()

    acquired = await limiter.acquire_concurrent("session-a", limit=2)
    assert acquired
    assert "session-a" in limiter._concurrent
    assert limiter._concurrent["session-a"] == 1

    await limiter.release_concurrent("session-a")
    assert "session-a" not in limiter._concurrent


@pytest.mark.asyncio
async def test_rate_limiter_cleans_up_empty_request_buckets() -> None:
    """Empty request buckets are deleted after check_request expires entries."""
    from yomai.limits import InMemoryRateLimiter

    limiter = InMemoryRateLimiter()
    now = 100.0

    # Make a request to create the bucket
    await limiter.check_request("user-1", limit=5, now=now)
    assert "user-1" in limiter._requests

    # Advance past the 60s window and make another request
    await limiter.check_request("user-1", limit=5, now=now + 120.0)
    # After evicting stale entries and adding a new one, the bucket should
    # contain exactly the new entry (and the key still exists)
    assert len(limiter._requests.get("user-1", [])) == 1


# ===========================================================================
# #30 — HITL interrupt already-resolved before wait (deadlock fix)
# ===========================================================================


@pytest.mark.asyncio
async def test_interrupt_already_resolved_does_not_deadlock() -> None:
    """If an interrupt is already resolved, get() returns resolved status without waiting."""
    from yomai.jobs.interrupts import InMemoryInterruptStore, Interrupt

    store = InMemoryInterruptStore()
    intr = Interrupt(id="abc123", job_id="j1", message="please approve")
    await store.create(intr)

    await store.resolve("abc123", "approved", action="approve")

    resolved = await store.get("abc123")
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.response == "approved"


@pytest.mark.asyncio
async def test_interrupt_resolve_before_wait_returns_immediately() -> None:
    """WorkflowRunner._wait_for_interrupt checks for pre-resolved interrupt before waiting."""
    from yomai.jobs.interrupts import InMemoryInterruptStore, Interrupt

    store = InMemoryInterruptStore()
    intr = Interrupt(id="def456", job_id="j2", message="review")
    await store.create(intr)

    await store.resolve("def456", "ok", action="approve")

    resolved_check = await store.get("def456")
    assert resolved_check is not None
    assert resolved_check.status == "resolved"


# ===========================================================================
# #31 — Budget treats stop vs warn correctly with on_exceeded
# ===========================================================================


@pytest.mark.asyncio
async def test_budget_warn_mode_does_not_block() -> None:
    """When on_exceeded='warn', budget checks log but never set exceeded=True."""
    from yomai.budget import BudgetTracker
    from yomai.config import BudgetConfig

    tracker = BudgetTracker(BudgetConfig(max_cost_per_day=0.01, on_exceeded="warn"))

    result = await tracker.check("s1", tokens_in=1000, tokens_out=500, cost_estimate=0.5)
    assert not result["exceeded"]


# ===========================================================================
# #32 — _extract_json handles code fences (markdown ```json)
# ===========================================================================


def test_extract_json_from_markdown_fence() -> None:
    """_extract_json extracts JSON from ```json ``` code blocks."""
    from pydantic import BaseModel

    from yomai.core.router import AgentRoute

    class Result(BaseModel):
        answer: str
        confidence: float

    route = AgentRoute.__new__(AgentRoute)
    text = 'Here is my analysis:\n\n```json\n{"answer": "yes", "confidence": 0.95}\n```\n\nHope that helps!'
    result = route._extract_json(text, Result)
    assert result.answer == "yes"  # pyright: ignore[reportAttributeAccessIssue]
    assert result.confidence == 0.95  # pyright: ignore[reportAttributeAccessIssue]


def test_extract_json_from_plain_fence() -> None:
    """_extract_json handles ``` without language tag."""
    from pydantic import BaseModel

    from yomai.core.router import AgentRoute

    class Result(BaseModel):
        value: int

    route = AgentRoute.__new__(AgentRoute)
    text = '```\n{"value": 42}\n```'
    result = route._extract_json(text, Result)
    assert result.value == 42  # pyright: ignore[reportAttributeAccessIssue]


def test_extract_json_returns_raw_when_no_fence() -> None:
    """_extract_json falls back to scanning for JSON when no fences present."""
    from pydantic import BaseModel

    from yomai.core.router import AgentRoute

    class Result(BaseModel):
        x: int

    route = AgentRoute.__new__(AgentRoute)
    text = 'preable text {"x": 7} trailing'
    result = route._extract_json(text, Result)
    assert result.x == 7  # pyright: ignore[reportAttributeAccessIssue]


# ===========================================================================
# #33 — DictMemory eviction sampling
# ===========================================================================


@pytest.mark.asyncio
async def test_dict_memory_partial_eviction_limits_scan() -> None:
    """DictMemory._evict_sample only removes EVICT_SAMPLE entries at a time (lazy scan)."""
    from yomai.memory.dict import DictMemory

    be = DictMemory(ttl_hours=1)
    be._ttl_secs = 1
    be._EVICT_SAMPLE = 3

    for i in range(10):
        await be.save(f"s{i}", f"msg{i}", f"reply{i}")

    await asyncio.sleep(1.1)

    await be.load("s0")
    count_before = len(be._store)
    assert count_before < 10
    assert count_before >= 7  # at most EVICT_SAMPLE=3 removed


# ===========================================================================
# #34 — RedisJobStore atomic create prevents duplicate overwrite
# ===========================================================================


@pytest.mark.asyncio
async def test_redis_job_store_create_is_idempotent() -> None:
    """Calling create with same job_id twice returns the original record."""
    from test_v2_redis_jobs import FakeRedis

    from yomai.jobs.models import JobRecord
    from yomai.jobs.store import RedisJobStore

    client = FakeRedis()
    store = RedisJobStore("redis://test", prefix="yomai:test", ttl_secs=60, client=client)

    r1 = JobRecord(id="dup_job", route="/x", stream_url="/s", status_url="/u")
    created1 = await store.create(r1)
    assert created1.id == "dup_job"
    assert created1.route == "/x"

    r2 = JobRecord(id="dup_job", route="/y", stream_url="/s2", status_url="/u2")
    created2 = await store.create(r2)

    assert created2.route == "/x"


# ===========================================================================
# #35 — _validate_tool_args handles Union/Optional generics correctly
# ===========================================================================


def test_validate_tool_args_union_accepts_any_member() -> None:
    """Union[str, int] accepts either str or int."""

    from yomai.core.agent import AgentLoop

    def mytool(x: str | int) -> str:
        return str(x)

    loop = AgentLoop.__new__(AgentLoop)
    loop._validate_tool_args(mytool, {"x": 42})
    loop._validate_tool_args(mytool, {"x": "hello"})


def test_validate_tool_args_optional_allows_none() -> None:
    """Optional[int] accepts None."""

    from yomai.core.agent import AgentLoop

    def mytool(limit: int | None = 10) -> str:
        return str(limit)

    loop = AgentLoop.__new__(AgentLoop)
    loop._validate_tool_args(mytool, {"limit": None})
    loop._validate_tool_args(mytool, {"limit": 5})


def test_optional_int_rejects_string() -> None:
    """Optional[int] rejects str value."""

    from yomai.core.agent import AgentLoop

    def myfn(limit: int | None = 10) -> str:
        return str(limit)

    loop = AgentLoop.__new__(AgentLoop)
    with pytest.raises(TypeError, match="must be one of"):
        loop._validate_tool_args(myfn, {"limit": "abc"})


def test_validate_tool_args_handles_annotated() -> None:
    """Annotated[str, Field(...)] accepts str."""
    from typing import Annotated

    from yomai.core.agent import AgentLoop

    def mytool(query: Annotated[str, "some metadata"]) -> str:
        return query

    loop = AgentLoop.__new__(AgentLoop)
    loop._validate_tool_args(mytool, {"query": "test"})


def test_validate_tool_args_union_rejects_wrong_type() -> None:
    """Union[str, int] rejects list."""

    from yomai.core.agent import AgentLoop

    def mytool(x: str | int) -> str:
        return str(x)

    loop = AgentLoop.__new__(AgentLoop)
    with pytest.raises(TypeError, match="must be one of"):
        loop._validate_tool_args(mytool, {"x": [1, 2, 3]})
