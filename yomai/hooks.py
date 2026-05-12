from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from yomai.log import get as _get_logger

_log = _get_logger("hooks")


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
        _log.debug("hook.emit %s", name, extra={"hook_name": name, **payload})
        for handler in list(self._handlers.get(name, [])):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _log.warning("hook.handler_failed %s", name, extra={"hook_name": name}, exc_info=True)
                continue

    def emit_background(self, name: str, **payload: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.emit(name, **payload))
