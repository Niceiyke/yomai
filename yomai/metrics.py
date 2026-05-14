"""Prometheus-compatible metrics for Yomai.

Optional dependency: install with ``yomai[metrics]`` or ``pip install prometheus-client``.

Enable by adding ``prometheus-client`` to your environment. The metrics endpoint
at ``/__yomai__/metrics`` returns Prometheus text format when the library is
available, falling back to JSON.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MetricsRegistry", "registry"]

_PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest  # type: ignore[import-not-found]

    _PROMETHEUS_AVAILABLE = True
except ImportError:
    # Stub types for when prometheus-client is not installed
    class _StubMetric:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def labels(self, **_: Any) -> _StubMetric:
            return self

        def inc(self, _amount: float = 1) -> None:
            pass

        def observe(self, _amount: float) -> None:
            pass

        def set(self, _value: float) -> None:
            pass

        def dec(self, _amount: float = 1) -> None:
            pass

    Counter = _StubMetric  # type: ignore[misc,assignment]
    Gauge = _StubMetric  # type: ignore[misc,assignment]
    Histogram = _StubMetric  # type: ignore[misc,assignment]

    def generate_latest():
        return b""  # type: ignore[assignment]


class MetricsRegistry:
    """Central registry for Prometheus metrics."""

    def __init__(self, *, prefix: str = "yomai") -> None:
        self.prefix = prefix

        # Request metrics
        self.requests_total = Counter(f"{prefix}_requests_total", "Total requests", ["method", "route", "status"])
        self.request_duration_seconds = Histogram(
            f"{prefix}_request_duration_seconds",
            "Request duration",
            ["method", "route"],
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
        )

        # LLM metrics
        self.llm_tokens_total = Counter(
            f"{prefix}_llm_tokens_total", "Total tokens consumed", ["provider", "model", "direction"]
        )
        self.llm_requests_total = Counter(
            f"{prefix}_llm_requests_total", "LLM requests", ["provider", "model", "status"]
        )

        # Tool metrics
        self.tool_calls_total = Counter(f"{prefix}_tool_calls_total", "Tool invocations", ["tool_name", "status"])
        self.tool_duration_seconds = Histogram(
            f"{prefix}_tool_duration_seconds",
            "Tool execution duration",
            ["tool_name"],
            buckets=(0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30),
        )

        # Connection / job gauges
        self.active_connections = Gauge(f"{prefix}_active_connections", "Active SSE connections")
        self.jobs_active = Gauge(f"{prefix}_jobs_active", "Active workflow jobs")
        self.errors_total = Counter(f"{prefix}_errors_total", "Total errors", ["type"])

    def get_metrics(self) -> bytes:
        """Return Prometheus text format metrics."""
        return generate_latest()

    @property
    def available(self) -> bool:
        return _PROMETHEUS_AVAILABLE


# Global singleton
registry = MetricsRegistry()
