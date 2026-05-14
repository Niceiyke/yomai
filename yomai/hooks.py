from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from yomai.log import get as _get_logger

_log = _get_logger("hooks")


@dataclass(frozen=True, slots=True)
class HookEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


HookHandler = Callable[[HookEvent], Awaitable[Any]]


class HookRegistry:
    """Registry for lifecycle event handlers.

    Handlers registered via ``on()`` are invoked concurrently via
    ``asyncio.gather`` when events are emitted.  Failures are logged
    and accumulated in ``pop_failures()`` without affecting other
    handlers or the main request flow.

    ``emit_background()`` schedules handlers as a fire-and-forget
    task — useful for telemetry, logging, or side-effects that should
    not block the response.

    Example::

        hooks = HookRegistry()

        @hooks.on("agent.done")
        async def log_usage(event): ...

        hooks.emit("agent.done", session_id="abc", tokens=42)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)
        self._failures: list[dict[str, Any]] = []

    def on(self, name: str, handler: HookHandler) -> HookHandler:
        self._handlers[name].append(handler)
        return handler

    async def emit(self, name: str, **payload: Any) -> list[dict[str, Any]]:
        """Emit a hook event, running all handlers concurrently. Returns list of failures."""
        event = HookEvent(name=name, payload=payload)
        handlers = list(self._handlers.get(name, []))
        if not handlers:
            return []

        _log.debug("hook.emit %s (%d handlers)", name, len(handlers), extra={"hook_name": name, **payload})

        async def _run(h: HookHandler, hname: str) -> dict[str, Any] | None:
            try:
                await h(event)
                return None
            except Exception:
                import sys

                exc = sys.exc_info()[1]
                _log.warning("hook.handler_failed %s", name, extra={"hook_name": name, "handler": hname}, exc_info=True)
                return {"handler": hname, "error": str(exc)}

        def _handler_name(h: HookHandler) -> str:
            return getattr(h, "__name__", str(h))

        results = await asyncio.gather(*(_run(h, _handler_name(h)) for h in handlers), return_exceptions=False)
        failures = [r for r in results if r is not None]
        self._failures.extend(failures)
        if len(self._failures) > 1000:
            self._failures = self._failures[-500:]
        return failures

    def emit_background(self, name: str, **payload: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.emit(name, **payload))
        task.add_done_callback(self._on_background_done)

    def _on_background_done(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.warning("hook.background_failed", extra={"error": str(exc)}, exc_info=exc)

    def pop_failures(self) -> list[dict[str, Any]]:
        """Return and clear accumulated handler failures across all hooks."""
        failures = self._failures[:]
        self._failures.clear()
        return failures
