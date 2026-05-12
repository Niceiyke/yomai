from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HookEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


HookHandler = Callable[[HookEvent], Any]


class HookRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)

    def on(self, name: str, handler: HookHandler) -> HookHandler:
        self._handlers[name].append(handler)
        return handler

    async def emit(self, name: str, **payload: Any) -> None:
        event = HookEvent(name=name, payload=payload)
        for handler in list(self._handlers.get(name, [])):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Hooks are intentionally best-effort. They must not break
                # request streams, workflow jobs, or worker execution.
                continue

    def emit_background(self, name: str, **payload: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.emit(name, **payload))
