from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TYPE_CHECKING

from yomai.core.agent import AgentLoop
from yomai.memory import MemoryBackend
from yomai.streaming.sse import sse_error
from yomai.tools.registry import ToolFunction
from yomai.workflow.events import sse_step_done, sse_step_start

if TYPE_CHECKING:
    from yomai.core.app import Yomai


class WorkflowRunner:
    def __init__(self, sse_queue: asyncio.Queue[str | None], session_id: str, memory: MemoryBackend, app: Yomai):
        self.sse_queue = sse_queue
        self.session_id = session_id
        self.memory = memory
        self.app = app
        self._step_index = 0

    async def step(self, name: str, agent_fn: Callable[..., Any], input: Any) -> str:
        self._step_index += 1
        index = self._step_index
        await self.sse_queue.put(sse_step_start(name, index, None))
        start = time.monotonic()

        tools = getattr(agent_fn, "_yomai_tools", [])
        if not isinstance(tools, list):
            tools = []
        typed_tools: list[ToolFunction] = tools

        history = await self.memory.load(self.session_id)
        agent_loop = AgentLoop(self.app._build_provider(), typed_tools, self.app.config.agent, self.app.config.llm)

        try:
            async for sse in agent_loop.run(str(input), history=history, system=""):
                await self.sse_queue.put(sse)
        except Exception as exc:
            await self.sse_queue.put(await sse_error(str(exc), exc.__class__.__name__))
            raise

        await self.memory.save(self.session_id, str(input), agent_loop.last_reply)
        duration_ms = int((time.monotonic() - start) * 1000)
        await self.sse_queue.put(sse_step_done(name, duration_ms))
        return agent_loop.last_reply

    async def parallel(self, steps: list[Awaitable[Any]]) -> list[Any]:
        return list(await asyncio.gather(*steps))
