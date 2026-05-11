from __future__ import annotations

import asyncio
import json
from typing import Any

SSEData = dict[str, Any]


def format_sse(event_type: str, data: SSEData) -> str:
    """Return a correctly formatted Server-Sent Event string."""
    return f"event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def sse_chunk(content: str) -> str:
    return format_sse("chunk", {"type": "chunk", "content": content})


async def sse_tool_start(name: str, args: dict[str, Any], id: str) -> str:
    return format_sse("tool_start", {"type": "tool_start", "name": name, "args": args, "id": id})


async def sse_tool_end(id: str, result: str, duration_ms: int) -> str:
    return format_sse("tool_end", {"type": "tool_end", "id": id, "result": result, "duration_ms": duration_ms})


async def sse_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
    return format_sse(
        "usage",
        {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd},
    )


async def sse_done() -> str:
    return format_sse("done", {"type": "done"})


async def sse_error(message: str, code: str = "error") -> str:
    return format_sse("error", {"type": "error", "message": message, "code": code})


async def sse_ping() -> str:
    return format_sse("ping", {})


async def heartbeat(queue: asyncio.Queue[str | None], interval_secs: int = 15) -> None:
    while True:
        await asyncio.sleep(interval_secs)
        await queue.put(await sse_ping())
