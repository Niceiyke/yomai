"""Retry logic for LLM provider calls."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

logger = logging.getLogger("yomai.llm.retry")

TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)

# Provider-specific rate-limit exception names (checked via class name)
_RATE_LIMIT_NAMES = frozenset({"RateLimitError", "RateLimit", "TooManyRequests"})


def _is_transient(exc: BaseException) -> bool:
    """Check if an exception is transient and worth retrying."""
    if isinstance(exc, TRANSIENT_EXCEPTIONS):
        return True
    name = exc.__class__.__name__
    if name in _RATE_LIMIT_NAMES:
        return True
    # Check HTTP status codes on the exception (common SDK pattern)
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status >= 500:
        return True
    return bool(isinstance(status, int) and status == 429)


async def retry_with_backoff(
    fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_retries: int = 3,
    backoff_secs: float = 1.0,
    multiplier: float = 2.0,
    **kwargs: Any,
) -> T:
    """Call an async function with exponential backoff on transient failures.

    Args:
        fn: Async callable to retry.
        max_retries: Maximum number of retry attempts (0 = no retry).
        backoff_secs: Initial backoff delay in seconds.
        multiplier: Backoff multiplier for each subsequent attempt.
    """
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_transient(exc) or attempt >= max_retries:
                raise
            delay = backoff_secs * (multiplier**attempt)
            logger.warning(
                "LLM call attempt %d/%d failed (%s), retrying in %.1fs",
                attempt + 1,
                max_retries + 1,
                exc.__class__.__name__,
                delay,
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc
