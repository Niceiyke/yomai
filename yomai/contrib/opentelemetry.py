"""OpenTelemetry tracing plugin for Yomai.

Usage::

    from yomai import Yomai
    from yomai.contrib.opentelemetry import setup
    app = Yomai(plugins=[setup])

    # Or directly:
    from yomai.contrib.opentelemetry import YomaiTracer
    tracer = YomaiTracer(service_name="my-app")
    tracer.setup(app)

Requires: ``pip install opentelemetry-api opentelemetry-sdk``
"""

from __future__ import annotations

from typing import Any

SPAN_KIND_INTERNAL = 1  # SpanKind.INTERNAL


class YomaiTracer:
    """Wraps OpenTelemetry tracing for Yomai lifecycle hooks."""

    def __init__(
        self,
        *,
        service_name: str = "yomai",
        exporter: Any = None,
    ) -> None:
        self.service_name = service_name
        self._tracer: Any = None
        self._exporter = exporter
        self._active_spans: dict[str, Any] = {}

    def _ensure_tracer(self) -> Any:
        if self._tracer is None:
            try:
                from opentelemetry import trace  # type: ignore[import-not-found]
                from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
                from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]

                provider = TracerProvider(resource=Resource.create({"service.name": self.service_name}))
                if self._exporter is not None:
                    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import-not-found]

                    provider.add_span_processor(BatchSpanProcessor(self._exporter))
                trace.set_tracer_provider(provider)
                self._tracer = trace.get_tracer(self.service_name)
            except ImportError:
                self._tracer = None
        return self._tracer

    def setup(self, app: Any) -> None:
        """Register hooks for tracing. Called automatically by Yomai(plugins=[setup])."""
        tracer = self._ensure_tracer()
        if tracer is None:
            return

        active: dict[str, Any] = self._active_spans

        async def on_agent_start(event: Any) -> None:
            sid = event.payload.get("session_id", "")
            span = tracer.start_span("agent.run", kind=SPAN_KIND_INTERNAL)
            span.set_attribute("session_id", sid)
            active[sid] = span

        async def on_agent_done(event: Any) -> None:
            sid = event.payload.get("session_id", "")
            span = active.pop(sid, None)
            if span:
                span.set_attribute("tokens_in", event.payload.get("tokens_in", 0))
                span.set_attribute("tokens_out", event.payload.get("tokens_out", 0))
                span.set_attribute("tool_calls", event.payload.get("tool_calls", 0))
                span.end()

        async def on_agent_error(event: Any) -> None:
            sid = event.payload.get("session_id", "")
            span = active.pop(sid, None)
            if span:
                span.set_attribute("error", True)
                span.set_attribute("error.message", str(event.payload.get("error", ""))[:500])
                span.end()

        async def on_tool_call(event: Any) -> None:
            sid = event.payload.get("session_id", "")
            parent = active.get(sid)
            span = tracer.start_span(f"tool.{event.payload.get('tool_name', 'unknown')}", kind=SPAN_KIND_INTERNAL)
            span.set_attribute("tool.name", event.payload.get("tool_name", ""))
            span.set_attribute("session_id", sid)
            if parent:
                span.set_attribute("parent_span", parent.get_span_context().span_id)
            key = f"{sid}:{event.payload.get('tool_id', '')}"
            active[key] = span

        async def on_tool_result(event: Any) -> None:
            sid = event.payload.get("session_id", "")
            key = f"{sid}:{event.payload.get('tool_id', '')}"
            span = active.pop(key, None)
            if span:
                span.set_attribute("tool.duration_ms", event.payload.get("duration_ms", 0))
                span.set_attribute("tool.error", event.payload.get("error", False))
                span.end()

        async def on_llm_call(event: Any) -> None:
            sid = event.payload.get("session_id", "")
            parent = active.get(sid)
            span = tracer.start_span("llm.call", kind=SPAN_KIND_INTERNAL)
            span.set_attribute("session_id", sid)
            span.set_attribute("llm.iteration", event.payload.get("iteration", 0))
            span.set_attribute("llm.tokens_in", event.payload.get("tokens_in", 0))
            span.set_attribute("llm.tokens_out", event.payload.get("tokens_out", 0))
            if parent:
                span.set_attribute("parent_span", parent.get_span_context().span_id)
            span.end()

        app.hooks.on("agent.start", on_agent_start)
        app.hooks.on("agent.done", on_agent_done)
        app.hooks.on("agent.error", on_agent_error)
        app.hooks.on("agent.tool_call", on_tool_call)
        app.hooks.on("agent.tool_result", on_tool_result)
        app.hooks.on("agent.llm_call", on_llm_call)


# Singleton-style convenience
setup = YomaiTracer().setup
