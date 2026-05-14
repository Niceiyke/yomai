from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from yomai.core.agent import AgentLoop
from yomai.streaming.sse import sse_tool_end


@dataclass(slots=True)
class CapturedToolCall:
    name: str
    args: dict[str, Any]
    result: str | None = None
    duration_ms: int = 0


@contextmanager
def capture_tools(return_value: str = "mocked tool result") -> Iterator[list[CapturedToolCall]]:
    """Record tool calls and return a fixed result without executing real tools."""
    calls: list[CapturedToolCall] = []
    original_execute = AgentLoop._execute_tool_call

    async def fake_execute(self, tool_call, messages, parent_llm_id):
        call = CapturedToolCall(name=tool_call.name, args=dict(tool_call.args), result=return_value)
        calls.append(call)
        result_str = str(return_value)
        messages.extend(self._tool_result_messages(tool_call, result_str))
        tool_id = f"tool_{tool_call.name}_{tool_call.id}"
        self._pending_tool_nodes.append(tool_id)
        yield sse_tool_end(tool_call.id, result_str, 0)

    AgentLoop._execute_tool_call = fake_execute  # type: ignore[method-assign]
    try:
        yield calls
    finally:
        AgentLoop._execute_tool_call = original_execute  # type: ignore[method-assign]
