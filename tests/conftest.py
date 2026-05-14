"""Pytest fixtures and configuration for Yomai tests."""

from __future__ import annotations

import os
from typing import Any

import pytest
import pytest_asyncio

from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.testing import YomaiTestClient

# Redis connection URL - can be overridden via env var
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")  # DB 15 for tests


@pytest.fixture
def redis_available() -> bool:
    """Check if Redis is available for integration tests."""
    if os.environ.get("TEST_REDIS_URL") == "none":
        return False
    try:
        import redis.asyncio  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.fixture
def skip_without_redis(request: pytest.FixtureRequest, redis_available: bool) -> None:
    """Skip test if Redis is not available."""
    if not redis_available:
        pytest.skip("Redis not available")


# type: ignore[misc] - pytest fixtures can have complex return types
@pytest_asyncio.fixture
async def redis_client() -> Any:
    """Get a Redis client for testing. Skips if Redis unavailable."""
    if os.environ.get("TEST_REDIS_URL") == "none":
        pytest.skip("Redis not available (TEST_REDIS_URL=none)")

    try:
        import redis.asyncio as redis_lib  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("redis package not installed")

    client = redis_lib.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()  # type: ignore[await-or-yield]
    except Exception as e:
        await client.aclose()
        pytest.skip(f"Redis not available: {e}")

    yield client

    # Cleanup: flush test database and close
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def app_factory() -> Any:
    """Factory for creating test apps with common configs."""
    from yomai import Yomai

    def create_app(
        llm: LLMConfig | None = None,
        memory: MemoryConfig | None = None,
        queue: QueueConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        return Yomai(
            llm=llm or LLMConfig(api_key=""),
            memory=memory or MemoryConfig(backend="dict", db_path="/unused"),
            queue=queue,
            **kwargs,
        )

    return create_app


@pytest_asyncio.fixture
async def test_client(app_factory: Any) -> Any:
    """Create a test client with a fresh app."""
    from yomai import Yomai
    from yomai.config import AgentConfig, LLMConfig, MemoryConfig

    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        agent=AgentConfig(max_tool_calls=5),
    )

    yield YomaiTestClient(app)


@pytest.fixture
def mock_llm_response() -> Any:
    """Fixture for mocking LLM responses."""
    from yomai.testing import mock_llm

    return mock_llm
