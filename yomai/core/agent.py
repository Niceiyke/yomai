from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import AsyncGenerator
from typing import Any, Protocol, cast

from yomai.config import AgentConfig, LLMConfig
from yomai.llm.base import Done, LLMProvider, Message, TextChunk, ToolCall, ToolSchema
from yomai.streaming.sse import sse_chunk, sse_done, sse_error, sse_tool_end, sse_tool_start, sse_usage
from yomai.tools.registry import ToolFunction, _registry


class ToolSchemaProvider(Protocol):
    def tool_schemas(self, tools: list[ToolFunction]) -> list[ToolSchema]: ...


class ToolMessageProvider(Protocol):
    def tool_result_messages(self, tool_call: ToolCall, result: str) -> list[Message]: ...


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        tools: list[ToolFunction],
        config: AgentConfig,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.config = config
        self.llm_config = llm_config
        self.last_reply: str = ""
        self.last_usage: Done = Done()
        self.strip_reasoning = llm_config.strip_reasoning if llm_config else False
        self._inside_reasoning = False
        self._tool_map = {getattr(tool, "tool_name", getattr(tool, "__name__", "")): tool for tool in tools}

    def _tool_schemas(self) -> list[ToolSchema]:
        if hasattr(self.provider, "tool_schemas"):
            provider = cast(ToolSchemaProvider, self.provider)
            return provider.tool_schemas(self.tools)
        return _registry.get_schemas_for_anthropic(self.tools)

    def _estimate_cost(self, usage: Done) -> float:
        if not self.llm_config:
            return 0.0
        return (
            usage.input_tokens * self.llm_config.cost_per_token.get("input", 0.0)
            + usage.output_tokens * self.llm_config.cost_per_token.get("output", 0.0)
        )

    async def run(self, message: str, history: list[Message], system: str = "") -> AsyncGenerator[str, None]:
        messages: list[Message] = [*history, {"role": "user", "content": message}]
        tool_schemas = self._tool_schemas()
        iterations = 0
        usage = Done()

        while iterations <= self.config.max_tool_calls:
            saw_tool_call = False

            async for event in self.provider.stream(messages, tool_schemas, system):
                if isinstance(event, TextChunk):
                    content = self._maybe_strip_reasoning(event.content)
                    self.last_reply += content
                    yield await sse_chunk(content)
                elif isinstance(event, ToolCall):
                    if iterations >= self.config.max_tool_calls:
                        yield await sse_error("Maximum tool calls reached", "max_tool_calls_exceeded")
                        saw_tool_call = False
                        break
                    saw_tool_call = True
                    async for sse in self._execute_tool_call(event, messages):
                        yield sse
                elif isinstance(event, Done):
                    usage.input_tokens += event.input_tokens
                    usage.output_tokens += event.output_tokens
                    self.last_usage = Done(usage.input_tokens, usage.output_tokens)

            if not saw_tool_call:
                break

            iterations += 1

        yield await sse_usage(usage.input_tokens, usage.output_tokens, self._estimate_cost(usage))
        yield await sse_done()

    def _maybe_strip_reasoning(self, text: str) -> str:
        if not self.strip_reasoning:
            return text
        output: list[str] = []
        i = 0
        while i < len(text):
            if not self._inside_reasoning and text.startswith("<think>", i):
                self._inside_reasoning = True
                i += len("<think>")
            elif self._inside_reasoning and text.startswith("</think>", i):
                self._inside_reasoning = False
                i += len("</think>")
            elif self._inside_reasoning:
                i += 1
            else:
                output.append(text[i])
                i += 1
        return "".join(output)

    async def _execute_tool_call(self, tool_call: ToolCall, messages: list[Message]) -> AsyncGenerator[str, None]:
        """Execute a tool immediately and stream tool_start/tool_end as soon as the call is observed."""
        yield await sse_tool_start(tool_call.name, tool_call.args, tool_call.id)
        start = time.monotonic()
        result: Any
        fn = _registry.get(tool_call.name) if tool_call.name in self._tool_map else None
        if fn is None:
            result = f"Error: Unknown tool {tool_call.name!r}"
            yield await sse_error(result, "unknown_tool")
        else:
            try:
                signature = inspect.signature(fn)
                bound = signature.bind(**tool_call.args)
                self._validate_tool_args(fn, bound.arguments)
                if inspect.iscoroutinefunction(fn):
                    result = await fn(**tool_call.args)
                else:
                    result = await asyncio.to_thread(functools.partial(fn, **tool_call.args))
            except Exception as exc:
                result = f"Error: {exc}"

        duration_ms = int((time.monotonic() - start) * 1000)
        result_str = str(result)
        yield await sse_tool_end(tool_call.id, result_str, duration_ms)
        messages.extend(self._tool_result_messages(tool_call, result_str))

    def _validate_tool_args(self, fn: ToolFunction, args: dict[str, Any]) -> None:
        hints = getattr(fn, "__annotations__", {})
        for name, value in args.items():
            expected = hints.get(name)
            if expected in (str, int, float, bool, list, dict) and not isinstance(value, expected):
                raise TypeError(f"Tool argument {name!r} must be {expected.__name__}")

    def _tool_result_messages(self, tool_call: ToolCall, result: str) -> list[Message]:
        formatter = getattr(self.provider, "tool_result_messages", None)
        if callable(formatter):
            provider = cast(ToolMessageProvider, self.provider)
            return provider.tool_result_messages(tool_call, result)
        return [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "input": tool_call.args,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call.id,
                        "content": result,
                    }
                ],
            },
        ]
