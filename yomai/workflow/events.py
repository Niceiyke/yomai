from __future__ import annotations

import json
from typing import Any

from yomai.streaming.sse import format_sse


def sse_step_start(name: str, index: int, of: int | None = None) -> str:
    return format_sse("step_start", {"type": "step_start", "name": name, "index": index, "of": of})


def sse_step_done(name: str, duration_ms: int) -> str:
    return format_sse("step_done", {"type": "step_done", "name": name, "duration_ms": duration_ms})


def sse_result(content: str | dict[str, Any] | list[Any]) -> str:
    if isinstance(content, str):
        value = content
    else:
        value = json.dumps(content, separators=(",", ":"))
    return format_sse("result", {"type": "result", "content": value})
