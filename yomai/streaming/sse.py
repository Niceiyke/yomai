from __future__ import annotations

import asyncio
import json
from typing import Any

SSEData = dict[str, Any]


def _sanitize_sse_value(obj: Any) -> Any:
    """Recursively replace newlines in string values to protect SSE framing."""
    if isinstance(obj, str):
        return obj.replace("\n", " ").replace("\r", "")
    if isinstance(obj, dict):
        return {k: _sanitize_sse_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_sse_value(v) for v in obj]
    return obj


def _encode_sse(data: SSEData) -> str:
    return json.dumps(_sanitize_sse_value(data), separators=(",", ":"))


def _sanitize_event_type(et: str) -> str:
    """SSE event type must not contain newlines or be empty."""
    cleaned = et.replace("\n", "").replace("\r", "").strip()
    if not cleaned:
        cleaned = "message"
    return cleaned


def format_sse(event_type: str, data: SSEData) -> str:
    """Return a correctly formatted Server-Sent Event string."""
    return f"event: {_sanitize_event_type(event_type)}\ndata: {_encode_sse(data)}\n\n"


def format_sse_with_id(event_id: int | str, event_type: str, data: SSEData) -> str:
    """Return a Server-Sent Event string with a replay id."""
    return f"id: {event_id}\nevent: {_sanitize_event_type(event_type)}\ndata: {_encode_sse(data)}\n\n"


def sse_chunk(content: str) -> str:
    return format_sse("chunk", {"type": "chunk", "content": content})


def sse_tool_start(name: str, args: dict[str, Any], id: str) -> str:
    return format_sse("tool_start", {"type": "tool_start", "name": name, "args": args, "id": id})


def sse_tool_end(id: str, result: str, duration_ms: int) -> str:
    return format_sse("tool_end", {"type": "tool_end", "id": id, "result": result, "duration_ms": duration_ms})


def sse_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
    return format_sse(
        "usage",
        {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd},
    )


def sse_done() -> str:
    return format_sse("done", {"type": "done"})


def sse_error(message: str, code: str = "error") -> str:
    return format_sse("error", {"type": "error", "message": message, "code": code})


def sse_ping() -> str:
    return format_sse("ping", {})


async def heartbeat(queue: asyncio.Queue[str | None], interval_secs: int = 15) -> None:
    while True:
        await asyncio.sleep(interval_secs)
        await queue.put(sse_ping())
