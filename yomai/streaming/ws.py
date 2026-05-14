"""WebSocket event codec for bidirectional real-time streaming.

Converts the same SSE event types to JSON frames over WebSocket,
enabling the same event schema on both transports.
"""

from __future__ import annotations

import json
from typing import Any


def ws_chunk(content: str) -> str:
    return json.dumps({"type": "chunk", "content": content})


def ws_tool_start(name: str, args: dict[str, Any], id: str) -> str:
    return json.dumps({"type": "tool_start", "name": name, "args": args, "id": id})


def ws_tool_end(id: str, result: str, duration_ms: int) -> str:
    return json.dumps({"type": "tool_end", "id": id, "result": result, "duration_ms": duration_ms})


def ws_tool_progress(id: str, message: str) -> str:
    return json.dumps({"type": "tool_progress", "id": id, "message": message})


def ws_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
    return json.dumps(
        {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd}
    )


def ws_done() -> str:
    return json.dumps({"type": "done"})


def ws_error(message: str, code: str = "error") -> str:
    return json.dumps({"type": "error", "message": message, "code": code})


def ws_step_start(name: str, index: int, of: int | None = None) -> str:
    payload = {"type": "step_start", "name": name, "index": index}
    if of is not None:
        payload["of"] = of
    return json.dumps(payload)


def ws_step_done(name: str, duration_ms: int) -> str:
    return json.dumps({"type": "step_done", "name": name, "duration_ms": duration_ms})


def ws_result(content: str) -> str:
    return json.dumps({"type": "result", "content": content})


def ws_interrupt(id: str, message: str) -> str:
    return json.dumps({"type": "interrupt", "id": id, "message": message})


def ws_ping() -> str:
    return json.dumps({"type": "ping"})


def ws_graph_upsert(
    id: str,
    label: str,
    kind: str,
    status: str = "running",
    *,
    parent: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "type": "graph",
        "action": "upsert",
        "id": id,
        "label": label,
        "kind": kind,
        "status": status,
    }
    if parent is not None:
        payload["parent"] = parent
    if meta is not None:
        payload["meta"] = meta
    return json.dumps(payload)


def ws_graph_edge(from_id: str, to_id: str, label: str = "") -> str:
    return json.dumps({"type": "graph", "action": "edge", "from": from_id, "to": to_id, "label": label})


def ws_graph_update(id: str, status: str, *, meta: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"type": "graph", "action": "update", "id": id, "status": status}
    if meta is not None:
        payload["meta"] = meta
    return json.dumps(payload)


def ws_graph_clear() -> str:
    return json.dumps({"type": "graph", "action": "clear"})


# WS-to-SSE bridge: client may send plain text or structured commands over WS
CMD_PING = "ping"
CMD_STOP = "stop"
CMD_MESSAGE = "message"


def parse_ws_message(raw: str | bytes) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "message", "content": raw}
