from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from yomai.tools.registry import ToolFunction, _registry


@dataclass(slots=True)
class CapturedToolCall:
    name: str
    args: dict[str, Any]
    result: str | None = None
    duration_ms: int = 0


@contextmanager
def capture_tools(return_value: str = "mocked tool result") -> Iterator[list[CapturedToolCall]]:
    """Record tool calls and return a fixed result without executing real tools."""
    calls: list[CapturedToolCall] = []
    original_get = _registry.get

    def fake_get(name: str) -> ToolFunction | None:
        original = original_get(name)
        if original is None:
            return None

        async def wrapper(**kwargs: Any) -> str:
            start = time.monotonic()
            call = CapturedToolCall(name=name, args=dict(kwargs), result=return_value)
            call.duration_ms = int((time.monotonic() - start) * 1000)
            calls.append(call)
            return return_value

        wrapper.__name__ = getattr(original, "__name__", name)
        return wrapper

    _registry.get = fake_get  # type: ignore[method-assign]
    try:
        yield calls
    finally:
        _registry.get = original_get  # type: ignore[method-assign]
