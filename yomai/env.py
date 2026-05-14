"""All environment variables consumed by Yomai, in one place.

Centralising these makes the full surface area of Yomai's env-var contract
discoverable and keeps defaults consistent.

These are module-level properties that read from ``os.environ`` on every
access so that tests can override values by modifying ``os.environ``
directly after import.
"""

from __future__ import annotations

import os


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_optional(key: str) -> str | None:
    return os.environ.get(key) or None


YOMAI_ENV: str
"""Set to ``"production"`` to hide error details, disable the dev playground,
and require an API key for metadata endpoints."""

YOMAI_HANDLE_SIGTERM: str
"""Set to ``"1"`` to enable graceful shutdown on SIGTERM."""

YOMAI_APP_TITLE: str
"""Title used in the OpenAPI schema and playground UI."""

YOMAI_API_KEY: str
"""API key required for ``/__yomai__/*`` metadata endpoints in production."""

ANTHROPIC_API_KEY: str
"""API key for the Anthropic provider."""

ANTHROPIC_BASE_URL: str | None
"""Custom base URL for Anthropic-compatible endpoints."""

OPENAI_API_KEY: str
"""API key for the OpenAI provider."""

OPENAI_BASE_URL: str | None
"""Custom base URL for OpenAI-compatible endpoints."""

REDIS_URL: str
"""Default Redis connection URL used by memory, queue, jobs, and rate-limiter backends."""


def __getattr__(name: str) -> str | None:
    if name == "YOMAI_ENV":
        return _get("YOMAI_ENV", "development")
    if name == "YOMAI_HANDLE_SIGTERM":
        return _get("YOMAI_HANDLE_SIGTERM", "")
    if name == "YOMAI_APP_TITLE":
        return _get("YOMAI_APP_TITLE", "Yomai Agent API")
    if name == "YOMAI_API_KEY":
        return _get("YOMAI_API_KEY", "")
    if name == "ANTHROPIC_API_KEY":
        return _get("ANTHROPIC_API_KEY", "")
    if name == "ANTHROPIC_BASE_URL":
        return _get_optional("ANTHROPIC_BASE_URL")
    if name == "OPENAI_API_KEY":
        return _get("OPENAI_API_KEY", "")
    if name == "OPENAI_BASE_URL":
        return _get_optional("OPENAI_BASE_URL")
    if name == "REDIS_URL":
        return _get("REDIS_URL", "redis://localhost:6379/0")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
