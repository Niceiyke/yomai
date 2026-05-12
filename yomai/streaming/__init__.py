"""Server-Sent Events (SSE) utilities for Yomai.

This module provides a unified interface for all SSE-related functionality:
- Core formatting functions
- Agent event helpers (chunk, tool_start, tool_end, usage, done, error)
- Workflow event helpers (step_start, step_done, result)
- Heartbeat support for long-running streams

Example:
    from yomai.streaming import format_sse, sse_chunk, sse_done

    # Format a custom SSE event
    sse = format_sse("custom", {"key": "value"})

    # Build SSE events for agent responses
    chunk_event = sse_chunk("Hello")
    done_event = sse_done()
"""
from __future__ import annotations

from typing import Any

from yomai.streaming.sse import (
    format_sse,
    format_sse_with_id,
    heartbeat,
    sse_chunk,
    sse_done,
    sse_error,
    sse_ping,
    sse_tool_end,
    sse_tool_start,
    sse_usage,
)

__all__ = [
    # Core formatting
    "format_sse",
    "format_sse_with_id",
    # Agent events
    "sse_chunk",
    "sse_tool_start",
    "sse_tool_end",
    "sse_usage",
    "sse_done",
    "sse_error",
    "sse_ping",
    # Utilities
    "heartbeat",
]

# Type alias for SSE event data
SSEData = dict[str, Any]

__version__ = "0.1.0"


# Lazy imports for workflow events to avoid circular dependencies
def __getattr__(name: str) -> Any:
    _workflow_attrs = {"sse_step_start", "sse_step_done", "sse_result"}
    if name in _workflow_attrs:
        from yomai.workflow.events import sse_result, sse_step_done, sse_step_start

        return {
            "sse_step_start": sse_step_start,
            "sse_step_done": sse_step_done,
            "sse_result": sse_result,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
