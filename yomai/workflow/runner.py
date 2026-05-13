from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from yomai.core.agent import AgentLoop
from yomai.jobs.checkpoints import StepCheckpoint
from yomai.jobs.interrupts import ApprovalResult, Interrupt
from yomai.memory import MemoryBackend
from yomai.streaming.sse import (
    sse_error,
    sse_graph_edge,
    sse_graph_update,
    sse_graph_upsert,
    sse_interrupt,
    sse_tool_progress,
)
from yomai.tools.cache import _cache as _tool_cache
from yomai.tools.registry import ToolFunction, _registry
from yomai.workflow.events import sse_step_done, sse_step_start

if TYPE_CHECKING:
    from yomai.core.app import Yomai


class WorkflowRunner:
    """Orchestrates agent steps, tools, branches, and delegation inside a workflow.

    Each workflow function receives a runner instance. The runner manages step
    ordering, checkpointing, cancellation, and a shared ``state`` dict that
    accumulates step outputs keyed by step name.
    """

    def __init__(
        self,
        sse_queue: asyncio.Queue[str | None],
        session_id: str,
        memory: MemoryBackend,
        app: Yomai,
        job_id: str | None = None,
    ):
        self.sse_queue = sse_queue
        self.session_id = session_id
        self.memory = memory
        self.app = app
        self.job_id = job_id
        self._step_index = 0
        self._prev_graph_node: str | None = None
        self.state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    async def cancelled(self) -> bool:
        if self.job_id is None:
            return False
        job = await self.app.jobs.get(self.job_id)
        return bool(job is not None and job.status == "cancelled")

    async def raise_if_cancelled(self) -> None:
        if await self.cancelled():
            raise asyncio.CancelledError("Workflow job cancelled")

    # ------------------------------------------------------------------
    # step() — run an agent, with retry and shared-state accumulation
    # ------------------------------------------------------------------

    async def step(
        self,
        name: str,
        agent_fn: Callable[..., Any],
        input: Any,
        *,
        retries: int = 0,
        backoff_secs: float = 1.0,
    ) -> str:
        """Run a named agent step.  On success the agent's last reply is stored
        in ``self.state[name]``.  Set *retries* > 0 to retry on failure with
        exponential backoff starting at *backoff_secs*.
        """
        await self.raise_if_cancelled()
        self._step_index += 1
        index = self._step_index
        step_id = f"step_{name}"
        input_hash = self._input_hash(input)

        self._emit_graph_node(step_id, name, "step")

        if self.job_id is not None:
            existing = await self.app.checkpoints.get(self.job_id, name, input_hash)
            if existing is not None and existing.status == "succeeded":
                self._emit_checkpoint(name, str(existing.result or ""))
                self.state[name] = existing.result or ""
                await self.sse_queue.put(sse_step_start(name, index, None))
                await self.sse_queue.put(sse_step_done(name, 0))
                return str(existing.result or "")

        await self.sse_queue.put(sse_step_start(name, index, None))
        start = time.monotonic()

        for attempt in range(retries + 1):
            if attempt > 0:
                delay = backoff_secs * (2 ** (attempt - 1))
                await asyncio.sleep(delay)
                await self.raise_if_cancelled()
                await self.sse_queue.put(
                    sse_graph_update(step_id, "running", meta={"attempt": attempt + 1, "retrying": True})
                )

            try:
                result = await self._run_agent(name, agent_fn, str(input))
                self.state[name] = result
                break
            except Exception as exc:
                if attempt < retries:
                    continue
                await self.sse_queue.put(sse_graph_update(step_id, "error", meta={"message": str(exc)[:200]}))
                await self.sse_queue.put(sse_error(str(exc), exc.__class__.__name__))
                raise

        duration_ms = int((time.monotonic() - start) * 1000)
        if self.job_id is not None:
            await self.app.checkpoints.save(
                StepCheckpoint(
                    job_id=self.job_id,
                    step=name,
                    input_hash=input_hash,
                    result=self.state[name],
                    duration_ms=duration_ms,
                )
            )
        await self.sse_queue.put(sse_graph_update(step_id, "done", meta={"duration_ms": duration_ms, "retries_used": retries}))
        await self.sse_queue.put(sse_step_done(name, duration_ms))
        return self.state[name]

    # ------------------------------------------------------------------
    # parallel() — concurrent agent steps
    # ------------------------------------------------------------------

    async def parallel(self, steps: list[Awaitable[Any]]) -> list[Any]:
        parallel_id = f"parallel_{self._step_index + 1}"
        self._emit_graph_node(parallel_id, f"parallel ({len(steps)})", "parallel")
        results = list(await asyncio.gather(*steps))
        await self.sse_queue.put(sse_graph_update(parallel_id, "done"))
        return results

    # ------------------------------------------------------------------
    # branch() — conditional routing
    # ------------------------------------------------------------------

    async def branch(
        self,
        name: str,
        *,
        condition: Callable[[dict[str, Any]], bool],
        on_true: Callable[[], Awaitable[Any]],
        on_false: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Evaluate *condition(self.state)* and execute either *on_true* or *on_false*.

        The branch is drawn as a graph node whose label shows which path was taken.
        Both callbacks are async and receive no arguments — use closures to pass data.
        """
        await self.raise_if_cancelled()
        branch_id = f"branch_{name}"
        taken = condition(self.state)
        label = f"{name} → {'true' if taken else 'false'}"
        self._emit_graph_node(branch_id, label, "parallel")

        if taken:
            coro = on_true()
            result = await coro
        else:
            coro = on_false()
            result = await coro

        await self.sse_queue.put(
            sse_graph_update(branch_id, "done", meta={"taken": "true" if taken else "false"})
        )
        return result

    # ------------------------------------------------------------------
    # tool() — direct tool execution (no LLM)
    # ------------------------------------------------------------------

    async def tool(self, fn: Callable[..., Any], /, **kwargs: Any) -> Any:
        """Call a ``@tool``-decorated function directly — no LLM agent involved.

        The tool execution appears as a ``tool`` node in the graph.
        Useful for deterministic operations: fetching data, writing to a DB,
        calling an external API.
        """
        tool_name = getattr(fn, "tool_name", getattr(fn, "__name__", "tool"))
        tool_id = f"tool_{tool_name}_{hashlib.md5(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()[:8]}"
        args_preview = ", ".join(f"{k}={v!r}" for k, v in list(kwargs.items())[:3])
        label = f"{tool_name}({args_preview})" if args_preview else tool_name

        self._emit_graph_node(tool_id, label[:80], "tool")
        start = time.monotonic()

        # Tool cache
        cache_ttl: int | None = getattr(fn, "_tool_cache_ttl", None)
        if cache_ttl is not None:
            cached = _tool_cache.get(tool_name, kwargs)
            if cached is not None:
                duration_ms = int((time.monotonic() - start) * 1000)
                result_str = str(cached)
                await self.sse_queue.put(
                    sse_graph_update(tool_id, "done",
                        meta={"result": result_str[:200], "duration_ms": duration_ms, "cached": True})
                )
                return cached

        try:
            if inspect.isasyncgenfunction(fn):
                # Streaming tool: async generator that yields progress, last yield = result
                chunks: list[str] = []
                async for chunk in fn(**kwargs):
                    chunk_str = str(chunk)
                    chunks.append(chunk_str)
                    await self.sse_queue.put(sse_tool_progress(tool_id, chunk_str))
                result = chunks[-1] if chunks else ""
            elif asyncio.iscoroutinefunction(fn):
                result = await fn(**kwargs)
            else:
                result = await asyncio.to_thread(fn, **kwargs)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self.sse_queue.put(
                sse_graph_update(tool_id, "error", meta={"message": str(exc)[:200], "duration_ms": duration_ms})
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        result_str = str(result)
        await self.sse_queue.put(
            sse_graph_update(tool_id, "done", meta={"result": result_str[:200], "duration_ms": duration_ms})
        )
        if cache_ttl is not None:
            _tool_cache.set(tool_name, kwargs, result, cache_ttl)
        return result

    # ------------------------------------------------------------------
    # delegate() — dynamic agent-to-agent orchestration
    # ------------------------------------------------------------------

    async def delegate(
        self,
        agent_fn: Callable[..., Any],
        prompt: str,
        *,
        system: str = "",
        tools: list[ToolFunction] | None = None,
    ) -> str:
        """Run *agent_fn* as a sub-call, streaming its output through the workflow SSE.

        The delegation appears as a ``step`` node in the graph labeled ``delegate: <name>``.
        The agent's last reply is returned and stored in ``self.state[agent_name]``.
        """
        agent_name = getattr(agent_fn, "__name__", "delegate")
        step_id = f"step_delegate_{agent_name}_{self._step_index}"
        self._step_index += 1
        self._emit_graph_node(step_id, f"delegate: {agent_name}", "step")

        history = await self.memory.load(self.session_id)
        _tools: list[ToolFunction] = list(tools or getattr(agent_fn, "_yomai_tools", []) or [])
        _tools.append(self._human_tool())
        loop = AgentLoop(
            self.app._build_provider(),
            _tools,
            self.app.config.agent,
            self.app.config.llm,
            budget_tracker=self.app.budget,
            session_id=self.session_id,
            hooks=self.app.hooks,
        )

        try:
            async for sse in loop.run(prompt, history=history, system=system):
                await self.raise_if_cancelled()
                await self.sse_queue.put(sse)
        except Exception as exc:
            await self.sse_queue.put(sse_graph_update(step_id, "error", meta={"message": str(exc)[:200]}))
            await self.sse_queue.put(sse_error(str(exc), exc.__class__.__name__))
            raise

        await self.memory.save(self.session_id, prompt, loop.last_reply)
        self.state[agent_name] = loop.last_reply
        await self.sse_queue.put(sse_graph_update(step_id, "done"))
        return loop.last_reply

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_graph_node(self, node_id: str, label: str, kind: str) -> None:
        self.sse_queue.put_nowait(sse_graph_upsert(node_id, label[:80], kind, "running"))
        if self._prev_graph_node:
            self.sse_queue.put_nowait(sse_graph_edge(self._prev_graph_node, node_id, "next"))
        self._prev_graph_node = node_id

    def _emit_checkpoint(self, name: str, result_preview: str) -> None:
        ckpt_id = f"checkpoint_{name}"
        self.sse_queue.put_nowait(
            sse_graph_upsert(ckpt_id, f"checkpoint: {name}", "checkpoint", "done",
                meta={"result": result_preview[:100]})
        )
        self.sse_queue.put_nowait(sse_graph_edge(self._prev_graph_node or f"step_{name}", ckpt_id, "replay"))

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    async def approve(
        self,
        message: str,
        *,
        content: str = "",
        timeout_secs: int | None = None,
    ) -> ApprovalResult:
        """Pause the workflow and wait for structured human approval.

        Emits an ``event: interrupt`` SSE event. The human resolves via
        ``POST /__yomai__/interrupts/{id}/resume`` with a JSON body::

            {"response": "Looks good", "action": "approve", "comment": "ship it"}

        Returns an ``ApprovalResult`` with ``.is_approved``, ``.is_rejected``,
        ``.action``, ``.comment``, and ``.resolved_by`` fields.

        Parameter *content* is included in the interrupt message for context
        (e.g., the draft text being reviewed) but does not affect the result.
        """
        full_message = message
        if content:
            full_message = f"{message}\n\n---\n{content[:500]}"
        await self.interrupt(full_message, timeout_secs=timeout_secs)

        # After resolution, look up the interrupt for structured data
        from datetime import datetime, timezone

        interrupts = self.app._interrupt_store._interrupts
        resolved = [i for i in interrupts.values() if i.status == "resolved" and i.response is not None]
        if resolved:
            latest = max(resolved, key=lambda i: i.resolved_at or datetime.min.replace(tzinfo=timezone.utc))
            return latest.to_approval()

        # Fallback: treat as approved
        return ApprovalResult(action="approved")

    async def interrupt(self, message: str, *, timeout_secs: int | None = None) -> str:
        """Pause the workflow and wait for a human response.

        Emits an ``event: interrupt`` SSE event with an interrupt ID.
        The human resolves it via ``POST /__yomai__/interrupts/{id}/resume``.
        Returns the human's text response.
        """
        import uuid

        interrupt_id = uuid.uuid4().hex[:12]
        intr = Interrupt(id=interrupt_id, job_id=self.job_id or "", message=message)
        await self.app._interrupt_store.create(intr)

        # Emit the interrupt SSE event so clients know to prompt the human
        await self.sse_queue.put(sse_interrupt(interrupt_id, message))

        # Wait for resolution
        is_redis = type(self.app._interrupt_store).__name__ == "RedisInterruptStore"
        try:
            if is_redis:
                # Poll Redis every 200ms until resolved
                deadline = asyncio.get_running_loop().time() + (timeout_secs or 3600)
                while asyncio.get_running_loop().time() < deadline:
                    resolved = await self.app._interrupt_store.get(interrupt_id)
                    if resolved and resolved.status == "resolved":
                        return resolved.response or ""
                    await asyncio.sleep(0.2)
                await self.app._interrupt_store.delete(interrupt_id)
                raise asyncio.TimeoutError(f"Interrupt {interrupt_id} timed out after {timeout_secs}s")
            else:
                event = self.app._interrupt_store.event(interrupt_id)
                if timeout_secs:
                    await asyncio.wait_for(event.wait(), timeout=timeout_secs)
                else:
                    await event.wait()
        except asyncio.TimeoutError as exc:
            await self.app._interrupt_store.delete(interrupt_id)
            raise asyncio.TimeoutError(f"Interrupt {interrupt_id} timed out after {timeout_secs}s") from exc

        resolved = await self.app._interrupt_store.get(interrupt_id)
        return resolved.response if resolved else ""

    def _human_tool(self) -> ToolFunction:
        """Return a ``@tool`` that the agent can call to request human input.

        Usage inside a workflow step agent::

            # The agent can now call this tool:
            #   - name: request_human_input
            #   - args: {"question": "Is this draft acceptable?"}
            #   - returns: the human's response string
        """

        async def request_human_input(question: str) -> str:
            """Ask a human for input or approval. Use when you are uncertain
            or need authorization before proceeding."""
            return await self.interrupt(question)

        # Build a tool schema so the LLM knows about it
        request_human_input.schema = {
            "name": "request_human_input",
            "description": "Ask a human for input or approval.",
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The question to ask the human"}},
            "required": ["question"],
        }
        request_human_input.tool_name = "request_human_input"
        request_human_input._tool_timeout_secs = None
        request_human_input._tool_max_retries = 0
        _registry.register(request_human_input)
        return request_human_input

    async def _run_agent(self, name: str, agent_fn: Callable[..., Any], input: str) -> str:
        tools = list(getattr(agent_fn, "_yomai_tools", []) or [])
        tools.append(self._human_tool())
        history = await self.memory.load(self.session_id)
        loop = AgentLoop(
            self.app._build_provider(), tools, self.app.config.agent, self.app.config.llm,
            budget_tracker=self.app.budget, session_id=self.session_id, hooks=self.app.hooks,
        )
        async for sse in loop.run(input, history=history, system=""):
            await self.raise_if_cancelled()
            await self.sse_queue.put(sse)
        await self.memory.save(self.session_id, input, loop.last_reply)
        return loop.last_reply

    def _input_hash(self, input: Any) -> str:
        try:
            payload = json.dumps(input, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            payload = str(input)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
