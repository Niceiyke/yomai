from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from yomai.streaming.schemas import (
    ChunkData,
    DoneData,
    ErrorData,
    GraphClearData,
    GraphEdgeData,
    GraphUpdateData,
    GraphUpsertData,
    InterruptData,
    PingData,
    ToolEndData,
    ToolProgressData,
    ToolStartData,
    UsageData,
)

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


def _sanitize_event_type(et: str) -> str:
    """SSE event type must not contain newlines or be empty."""
    cleaned = et.replace("\n", "").replace("\r", "").strip()
    if not cleaned:
        cleaned = "message"
    return cleaned


def _encode_sse(data: SSEData | BaseModel) -> str:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json", exclude_none=True, by_alias=True)
    return json.dumps(_sanitize_sse_value(data), separators=(",", ":"))


def format_sse(event_type: str, data: SSEData | BaseModel) -> str:
    """Return a correctly formatted Server-Sent Event string."""
    return f"event: {_sanitize_event_type(event_type)}\ndata: {_encode_sse(data)}\n\n"


def format_sse_with_id(event_id: int | str, event_type: str, data: SSEData | BaseModel) -> str:
    """Return a Server-Sent Event string with a replay id."""
    return f"id: {event_id}\nevent: {_sanitize_event_type(event_type)}\ndata: {_encode_sse(data)}\n\n"


def sse_chunk(content: str) -> str:
    return format_sse("chunk", ChunkData(content=content))


def sse_tool_start(name: str, args: dict[str, Any], id: str) -> str:
    return format_sse("tool_start", ToolStartData(name=name, args=args, id=id))


def sse_tool_end(id: str, result: str, duration_ms: int) -> str:
    return format_sse("tool_end", ToolEndData(id=id, result=result, duration_ms=duration_ms))


def sse_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
    return format_sse("usage", UsageData(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd))


def sse_done() -> str:
    return format_sse("done", DoneData())


def sse_error(message: str, code: str = "error") -> str:
    return format_sse("error", ErrorData(message=message, code=code))


def sse_ping() -> str:
    return format_sse("ping", PingData())


def sse_graph_upsert(
    id: str,
    label: str,
    kind: str,
    status: str = "running",
    *,
    parent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    return format_sse(
        "graph",
        GraphUpsertData(
            id=id,
            label=label,
            kind=kind,
            status=status,
            parent=parent,
            meta=meta,
        ),
    )


def sse_graph_edge(from_id: str, to_id: str, label: str = "") -> str:
    return format_sse("graph", GraphEdgeData(from_=from_id, to=to_id, label=label))


def sse_graph_update(id: str, status: str, *, meta: dict[str, Any] | None = None) -> str:
    return format_sse("graph", GraphUpdateData(id=id, status=status, meta=meta))


def sse_graph_clear() -> str:
    return format_sse("graph", GraphClearData())


def sse_interrupt(id: str, message: str) -> str:
    return format_sse("interrupt", InterruptData(id=id, message=message))


def sse_tool_progress(id: str, message: str) -> str:
    return format_sse("tool_progress", ToolProgressData(id=id, message=message))


async def heartbeat(queue: asyncio.Queue[str | None], interval_secs: int = 15) -> None:
    while True:
        await asyncio.sleep(interval_secs)
        await queue.put(sse_ping())
