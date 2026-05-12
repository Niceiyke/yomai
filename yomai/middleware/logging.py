from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from yomai.log import get as _get_logger

_log = _get_logger("middleware")


@dataclass(slots=True)
class ToolLog:
    name: str
    args: dict[str, Any]
    result: str = ""
    duration_ms: int = 0


@dataclass(slots=True)
class StreamLog:
    method: str
    path: str
    session_id: str
    route_type: str = "agent"
    started_at: float = field(default_factory=time.monotonic)
    tools: dict[str, ToolLog] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    errored: bool = False

    def observe_sse(self, sse: str) -> None:
        event_type = ""
        data_raw = "{}"
        for line in sse.splitlines():
            if line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_raw = line.removeprefix("data:").strip()
        try:
            data = json.loads(data_raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}

        if event_type == "tool_start":
            tool_id = str(data.get("id", ""))
            self.tools[tool_id] = ToolLog(
                name=str(data.get("name", "tool")),
                args=dict(data.get("args", {}) if isinstance(data.get("args"), dict) else {}),
            )
        elif event_type == "tool_end":
            tool_id = str(data.get("id", ""))
            tool = self.tools.get(tool_id)
            if tool is not None:
                tool.result = str(data.get("result", ""))
                tool.duration_ms = int(data.get("duration_ms", 0) or 0)
        elif event_type == "step_start":
            self.steps.append({"name": data.get("name", "step"), "index": data.get("index"), "started_at": time.monotonic()})
        elif event_type == "step_done":
            self.steps.append({"name": data.get("name", "step"), "duration_ms": data.get("duration_ms", 0), "done": True})
        elif event_type == "usage":
            self.input_tokens = int(data.get("input_tokens", 0) or 0)
            self.output_tokens = int(data.get("output_tokens", 0) or 0)
            self.cost_usd = float(data.get("cost_usd", 0.0) or 0.0)
        elif event_type == "error":
            self.errored = True

    def emit(self) -> None:
        elapsed = time.monotonic() - self.started_at
        tool_calls = [
            {"name": t.name, "args": t.args, "result": (t.result[:200] if len(t.result) > 200 else t.result),
             "duration_ms": t.duration_ms}
            for t in self.tools.values()
        ]
        workflow_steps = [
            {"name": s["name"], "duration_ms": s.get("duration_ms", 0)}
            for s in self.steps if s.get("done")
        ]
        _log.info(
            "%s %s",
            self.method, self.path,
            extra=_extra(
                route=self.path,
                session_id=self.session_id,
                method=self.method,
                duration_ms=round(elapsed * 1000),
                tokens_in=self.input_tokens,
                tokens_out=self.output_tokens,
                cost_usd=round(self.cost_usd, 6),
                errored=self.errored,
                tool_calls=tool_calls or None,
                workflow_steps=workflow_steps or None,
            ),
        )


class LoggingMiddleware(BaseHTTPMiddleware):
    """Request logger for non-streaming routes."""

    def __init__(self, app: Any, enabled: bool = True) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if self.enabled and request.method != "POST" and not request.url.path.startswith("/__yomai__"):
            _log.info(
                "%s %s", request.method, request.url.path,
                extra=_extra(method=request.method, route=request.url.path, status_code=response.status_code),
            )
        return response


def _extra(**kwargs: Any) -> dict[str, Any]:
    """Build extra dict for structured logging, dropping None values."""
    return {k: v for k, v in kwargs.items() if v is not None}
