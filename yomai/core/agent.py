from __future__ import annotations

import asyncio
import functools
import inspect
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Protocol, cast, get_args, get_origin

if TYPE_CHECKING:
    from yomai.budget import BudgetTracker
    from yomai.hooks import HookRegistry

from yomai.config import AgentConfig, LLMConfig
from yomai.llm.base import Done, LLMProvider, Message, TextChunk, ToolCall, ToolSchema
from yomai.streaming.sse import (
    sse_chunk,
    sse_done,
    sse_error,
    sse_graph_edge,
    sse_graph_update,
    sse_graph_upsert,
    sse_tool_end,
    sse_tool_progress,
    sse_tool_start,
    sse_usage,
)
from yomai.tools.cache import _cache as _tool_cache
from yomai.tools.registry import ToolFunction, _registry


def _message_text(message: str | list[dict[str, Any]]) -> str:
    """Return a plain-text representation of the message for storage/graphs."""
    if isinstance(message, str):
        return message
    parts: list[str] = []
    for block in message:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "image_url":
                parts.append("[image]")
            elif block.get("type") == "input_audio":
                parts.append("[audio]")
    return " ".join(parts) if parts else "[multi-modal]"


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
        *,
        budget_tracker: BudgetTracker | None = None,
        session_id: str = "",
        hooks: HookRegistry | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.config = config
        self.llm_config = llm_config
        self.budget_tracker = budget_tracker
        self.session_id = session_id
        self.hooks = hooks
        self.last_reply: str = ""
        self.last_usage: Done = Done()
        self.strip_reasoning = llm_config.strip_reasoning if llm_config else False
        self._inside_reasoning = False
        self._pending_tool_nodes: list[str] = []
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

    async def run(self, message: str | list[dict[str, Any]], history: list[Message], system: str = "") -> AsyncGenerator[str, None]:
        messages: list[Message] = [*history, {"role": "user", "content": message}]
        tool_schemas = self._tool_schemas()
        iterations = 0
        usage = Done()
        model_label = self.llm_config.model if self.llm_config else "llm"
        tool_count = 0

        # Display label for graph/hooks
        if isinstance(message, str):
            msg_label = message
        else:
            msg_label = next((b.get("text", "") for b in message if isinstance(b, dict) and b.get("type") == "text"), "[multi-modal]")

        if self.hooks is not None:
            self.hooks.emit_background("agent.start", session_id=self.session_id, message=msg_label[:200])
            self.hooks.emit_background("request.start", session_id=self.session_id)

        user_label = msg_label[:80] + ("..." if len(msg_label) > 80 else "")
        yield sse_graph_upsert("user_msg", user_label, "user_msg", "done")

        try:
            while iterations <= self.config.max_tool_calls:
                saw_tool_call = False
                llm_id = f"llm_{iterations}"
                yield sse_graph_upsert(llm_id, f"LLM: {model_label}", "llm", "running")

                if iterations == 0:
                    yield sse_graph_edge("user_msg", llm_id, "prompt")
                elif self._pending_tool_nodes:
                    for tool_id in self._pending_tool_nodes:
                        yield sse_graph_edge(tool_id, llm_id, "tool_result")
                self._pending_tool_nodes.clear()

                llm_start_tokens = (usage.input_tokens, usage.output_tokens)

                async for event in self.provider.stream(messages, tool_schemas, system):
                    if isinstance(event, TextChunk):
                        content = self._maybe_strip_reasoning(event.content)
                        self.last_reply += content
                        yield sse_chunk(content)
                        if self.hooks is not None:
                            self.hooks.emit_background("agent.chunk", session_id=self.session_id, content=content[:200])
                    elif isinstance(event, ToolCall):
                        if iterations >= self.config.max_tool_calls:
                            yield sse_error("Maximum tool calls reached", "max_tool_calls_exceeded")
                            if self.hooks is not None:
                                self.hooks.emit_background("agent.error", session_id=self.session_id,
                                    error="max_tool_calls_exceeded")
                            saw_tool_call = False
                            break
                        saw_tool_call = True
                        tool_count += 1
                        async for sse in self._execute_tool_call(event, messages, llm_id):
                            yield sse
                    elif isinstance(event, Done):
                        usage.input_tokens += event.input_tokens
                        usage.output_tokens += event.output_tokens
                        self.last_usage = Done(usage.input_tokens, usage.output_tokens)

                        # Budget check
                        if self.budget_tracker and self.session_id:
                            result = self.budget_tracker.check(
                                self.session_id,
                                event.input_tokens,
                                event.output_tokens,
                                self._estimate_cost(event),
                            )
                            if result["exceeded"]:
                                msg = f"Budget exceeded: {result.get('reason', 'limit')}"
                                yield sse_graph_update(llm_id, "error", meta={"reason": result.get("reason", "limit")})
                                yield sse_error(msg, "budget_exceeded")
                                yield sse_usage(usage.input_tokens, usage.output_tokens, self._estimate_cost(usage))
                                yield sse_done()
                                if self.hooks is not None:
                                    self.hooks.emit_background("agent.budget_exceeded", session_id=self.session_id,
                                        reason=result.get("reason", "limit"),
                                        tokens_in=usage.input_tokens, tokens_out=usage.output_tokens)
                                    self.hooks.emit_background("agent.done", session_id=self.session_id,
                                        tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
                                        tool_calls=tool_count)
                                    self.hooks.emit_background("request.end", session_id=self.session_id,
                                        status="budget_exceeded")
                                return

                this_in = usage.input_tokens - llm_start_tokens[0]
                this_out = usage.output_tokens - llm_start_tokens[1]
                yield sse_graph_update(
                    llm_id,
                    "done",
                    meta={"tokens_in": usage.input_tokens, "tokens_out": usage.output_tokens},
                )
                if self.hooks is not None:
                    self.hooks.emit_background("agent.llm_call", session_id=self.session_id,
                        iteration=iterations, tokens_in=this_in, tokens_out=this_out)

                if not saw_tool_call:
                    break

                iterations += 1

            # Emit response node
            resp_label = self.last_reply[:80] + ("..." if len(self.last_reply) > 80 else "")
            yield sse_graph_upsert("response", resp_label or "(empty)", "response", "done")
            yield sse_graph_edge(f"llm_{iterations}", "response", "output")

            yield sse_usage(usage.input_tokens, usage.output_tokens, self._estimate_cost(usage))
            yield sse_done()

            if self.hooks is not None:
                self.hooks.emit_background("agent.done", session_id=self.session_id,
                    tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
                    tool_calls=tool_count, iterations=iterations)
                self.hooks.emit_background("request.end", session_id=self.session_id, status="ok")

        except Exception as exc:
            if self.hooks is not None:
                self.hooks.emit_background("agent.error", session_id=self.session_id,
                    error=str(exc)[:200], error_type=exc.__class__.__name__)
                self.hooks.emit_background("request.end", session_id=self.session_id,
                    status="error", error=str(exc)[:200])
            raise

    def _maybe_strip_reasoning(self, text: str) -> str:
        if not self.strip_reasoning:
            return text

        if self._inside_reasoning:
            text = "<think>" + text

        output: list[str] = []
        pos = 0
        while pos < len(text):
            start = text.find("<think>", pos)
            if start == -1:
                output.append(text[pos:])
                break
            output.append(text[pos:start])
            end = text.find("</think>", start + 7)
            if end == -1:
                self._inside_reasoning = True
                break
            self._inside_reasoning = False
            pos = end + 8

        return "".join(output)

    async def _execute_tool_call(
        self, tool_call: ToolCall, messages: list[Message], parent_llm_id: str
    ) -> AsyncGenerator[str, None]:
        """Execute a tool immediately and stream tool_start/tool_end as soon as the call is observed."""
        tool_id = f"tool_{tool_call.name}_{tool_call.id}"
        args_preview = ", ".join(f"{k}={v!r}" for k, v in list(tool_call.args.items())[:3])
        tool_label = f"{tool_call.name}({args_preview})" if args_preview else tool_call.name

        if self.hooks is not None:
            self.hooks.emit_background("agent.tool_call", session_id=self.session_id,
                tool_name=tool_call.name, tool_id=tool_call.id, args=tool_call.args)

        yield sse_graph_upsert(tool_id, tool_label[:80], "tool", "running")
        yield sse_graph_edge(parent_llm_id, tool_id, "tool_call")

        yield sse_tool_start(tool_call.name, tool_call.args, tool_call.id)
        start = time.monotonic()
        result: Any
        fn = _registry.get(tool_call.name) if tool_call.name in self._tool_map else None

        # Progress callback for streaming tools
        async def _emit_progress(msg: str) -> None:
            """Called by tools to stream intermediate progress."""
            # This is a closure capturing the parent generator — we can't await put_sse
            # directly since _execute_tool_call is itself an async generator.
            # We queue it via the graph update mechanism instead.
            pass  # Implemented via sse_tool_progress in the caller

        # Check tool cache
        cache_ttl: int | None = getattr(fn, "_tool_cache_ttl", None) if fn is not None else None
        if cache_ttl is not None and fn is not None:
            cached = _tool_cache.get(tool_call.name, tool_call.args)
            if cached is not None:
                result = cached
                duration_ms = int((time.monotonic() - start) * 1000)
                result_str = str(result)
                yield sse_graph_update(
                    tool_id, "done",
                    meta={"result": result_str[:200], "duration_ms": duration_ms, "cached": True},
                )
                yield sse_tool_end(tool_call.id, result_str, duration_ms)
                messages.extend(self._tool_result_messages(tool_call, result_str))
                self._pending_tool_nodes.append(tool_id)
                if self.hooks is not None:
                    self.hooks.emit_background("agent.tool_result", session_id=self.session_id,
                        tool_name=tool_call.name, tool_id=tool_call.id,
                        result=result_str[:200], duration_ms=duration_ms, error=False)
                return

        if fn is None:
            result = f"Error: Unknown tool {tool_call.name!r}"
            yield sse_graph_update(tool_id, "error", meta={"message": result})
            yield sse_error(result, "unknown_tool")
        else:
            timeout = getattr(fn, "_tool_timeout_secs", None)
            max_retries = getattr(fn, "_tool_max_retries", 0)

            for attempt in range(max_retries + 1):
                try:
                    signature = inspect.signature(fn)
                    bound = signature.bind(**tool_call.args)
                    self._validate_tool_args(fn, bound.arguments)
                    if inspect.isasyncgenfunction(fn):
                        # Streaming tool: async generator, last yield = result
                        chunks: list[str] = []
                        async for chunk in fn(**tool_call.args):
                            chunk_str = str(chunk)
                            chunks.append(chunk_str)
                            yield sse_tool_progress(tool_call.id, chunk_str)
                        result = chunks[-1] if chunks else ""
                    elif inspect.iscoroutinefunction(fn):
                        if timeout:
                            result = await asyncio.wait_for(fn(**tool_call.args), timeout=timeout)
                        else:
                            result = await fn(**tool_call.args)
                    else:
                        if timeout:
                            result = await asyncio.wait_for(
                                asyncio.to_thread(functools.partial(fn, **tool_call.args)),
                                timeout=timeout,
                            )
                        else:
                            result = await asyncio.to_thread(functools.partial(fn, **tool_call.args))
                    break  # Success
                except asyncio.TimeoutError:
                    result = f"Error: Tool {tool_call.name!r} timed out after {timeout}s"
                    if attempt < max_retries:
                        continue
                except Exception as exc:
                    result = f"Error: {exc}"
                    if attempt < max_retries:
                        continue

        duration_ms = int((time.monotonic() - start) * 1000)
        result_str = str(result)
        is_error = result_str.startswith("Error:")
        yield sse_graph_update(
            tool_id,
            "error" if is_error else "done",
            meta={"result": result_str[:200], "duration_ms": duration_ms},
        )
        yield sse_tool_end(tool_call.id, result_str, duration_ms)
        messages.extend(self._tool_result_messages(tool_call, result_str))
        self._pending_tool_nodes.append(tool_id)

        # Cache successful results
        if cache_ttl is not None and fn is not None and not is_error:
            _tool_cache.set(tool_call.name, tool_call.args, result, cache_ttl)

        if self.hooks is not None:
            self.hooks.emit_background("agent.tool_result", session_id=self.session_id,
                tool_name=tool_call.name, tool_id=tool_call.id,
                result=result_str[:200], duration_ms=duration_ms, error=is_error)

    def _validate_tool_args(self, fn: ToolFunction, args: dict[str, Any]) -> None:
        hints = getattr(fn, "__annotations__", {})
        for name, value in args.items():
            expected = hints.get(name)
            if expected is None:
                continue

            origin = get_origin(expected)
            arg_types = get_args(expected)

            # Generic list[T]
            if origin is list and arg_types:
                if not isinstance(value, list):
                    raise TypeError(f"Tool argument {name!r} must be a list")
                item_type = arg_types[0]
                if isinstance(item_type, type):
                    for item in value:
                        if not isinstance(item, item_type):
                            raise TypeError(f"Tool argument {name!r} items must be {item_type.__name__}")
                continue

            # Generic dict[K, V]
            if origin is dict and len(arg_types) == 2:
                if not isinstance(value, dict):
                    raise TypeError(f"Tool argument {name!r} must be a dict")
                continue

            # Union / Optional[T]
            if origin is not None and type(None) in arg_types:
                if value is not None:
                    non_none = [a for a in arg_types if a is not type(None)]
                    if non_none and isinstance(non_none[0], type) and not isinstance(value, non_none[0]):
                        raise TypeError(f"Tool argument {name!r} must be {non_none[0].__name__}")
                continue

            # Bare type
            if isinstance(expected, type) and not isinstance(value, expected):
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
