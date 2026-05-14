"""Yomai — a streaming-first framework for serving LLM agents over HTTP.

Quick start::

    from yomai import Yomai, tool
    from yomai.config import LLMConfig

    app = Yomai(llm=LLMConfig(provider="openai", model="gpt-4o-mini"))

    @tool
    def get_weather(city: str) -> str: ...

    @app.agent("/chat", tools=[get_weather])
    async def chat(message: str) -> None: ...
"""

from yomai.agents.routing import AgentCallError, AgentRegistry, agent_tool
from yomai.core.app import Depends, RouteGroup, Yomai
from yomai.hooks import HookEvent
from yomai.plugins import PluginSetup, plugin

# SSE utilities - for convenience when building streaming endpoints
from yomai.streaming import (
    format_sse,
    sse_chunk,
    sse_done,
    sse_error,
    sse_graph_clear,
    sse_graph_edge,
    sse_graph_update,
    sse_graph_upsert,
    sse_interrupt,
    sse_ping,
    sse_tool_end,
    sse_tool_start,
    sse_usage,
)
from yomai.tools.decorator import tool

__all__ = [
    "Yomai",
    "tool",
    "Depends",
    "RouteGroup",
    "HookEvent",
    "AgentRegistry",
    "agent_tool",
    "AgentCallError",
    # SSE utilities
    "format_sse",
    "sse_chunk",
    "sse_done",
    "sse_error",
    "sse_ping",
    "sse_tool_end",
    "sse_tool_start",
    "sse_usage",
    # Graph events
    "sse_graph_upsert",
    "sse_graph_edge",
    "sse_graph_update",
    "sse_graph_clear",
    "sse_interrupt",
    # Plugin system
    "plugin",
    "PluginSetup",
]
__version__ = "0.3.0"
