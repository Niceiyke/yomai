from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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
        status = "✗" if self.errored else "✓"
        print(f"[{time.strftime('%H:%M:%S')}] {self.method} {self.path}  session={self.session_id}")
        for tool in self.tools.values():
            args = ", ".join(f"{k}={v!r}" for k, v in tool.args.items())
            result = tool.result if len(tool.result) <= 80 else tool.result[:77] + "..."
            print(f"           ⚙ {tool.name}({args})  →  {result!r}  {tool.duration_ms}ms")
        for step in self.steps:
            if step.get("done"):
                print(f"           ▸ step {step.get('name')}  {step.get('duration_ms', 0)}ms  ✓")
        token_part = f"{self.input_tokens}→{self.output_tokens} tokens" if self.input_tokens or self.output_tokens else "tokens n/a"
        print(f"           {status} {elapsed:.1f}s  ·  {token_part}  ·  ~${self.cost_usd:.6f} (est.)")


class LoggingMiddleware(BaseHTTPMiddleware):
    """Minimal fallback request logger.

    Streaming routes use `StreamLog` directly so they can observe SSE events.
    This middleware intentionally avoids duplicate POST logs for Yomai routes.
    """

    def __init__(self, app: Any, enabled: bool = True) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        if self.enabled and request.method != "POST" and not request.url.path.startswith("/__yomai__"):
            print(f"[{time.strftime('%H:%M:%S')}] {request.method} {request.url.path}  status={response.status_code}")
        return response
