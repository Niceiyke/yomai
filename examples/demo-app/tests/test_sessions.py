"""Tests for session management endpoints."""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_get_session_empty() -> None:
    """GET /sessions/{session_id} returns empty history for new session."""
    from typing import Any, cast

    from app.agents.researcher import app

    # Clear any existing data
    await app.memory.clear("test-session-empty")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/sessions/test-session-empty")

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "test-session-empty"
    assert data["message_count"] == 0
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_get_session_with_messages() -> None:
    """GET /sessions/{session_id} returns message history."""
    from typing import Any, cast

    from app.agents.researcher import app

    # Clear and populate
    await app.memory.clear("test-session-messages")
    await app.memory.save("test-session-messages", "Hello", "Hi there!")
    await app.memory.save("test-session-messages", "How are you?", "I'm doing great!")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/sessions/test-session-messages")

    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "test-session-messages"
    assert data["message_count"] == 4  # 2 user + 2 assistant
    assert len(data["messages"]) == 4


@pytest.mark.asyncio
async def test_delete_session_requires_auth() -> None:
    """DELETE /sessions/{session_id} returns 401 without Bearer token."""
    from typing import Any, cast

    from app.agents.researcher import app

    # Populate session
    await app.memory.save("test-session-delete", "test", "data")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Without auth
        resp = await client.delete("/sessions/test-session-delete")
        assert resp.status_code == 401

        # With wrong auth
        resp = await client.delete(
            "/sessions/test-session-delete",
            headers={"Authorization": "Basic wrong"},
        )
        assert resp.status_code == 401

        # With correct auth (empty or any Bearer)
        resp = await client.delete(
            "/sessions/test-session-delete",
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_session_clears_data() -> None:
    """DELETE /sessions/{session_id} clears session data."""
    from typing import Any, cast

    from app.agents.researcher import app

    # Populate session
    await app.memory.save("test-session-clear", "test", "data")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/sessions/test-session-clear",
            headers={"Authorization": "Bearer secret"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == "test-session-clear"
    assert data["message_count"] == 0

    # Verify it's actually cleared
    history = await app.memory.load("test-session-clear")
    assert len(history) == 0


@pytest.mark.asyncio
async def test_session_with_research_agent() -> None:
    """Research agent properly saves session history."""
    from typing import Any, cast

    from app.agents.researcher import app
    from yomai.testing import YomaiTestClient, mock_llm

    # Clear session
    session_id = "test-research-session"
    await app.memory.clear(session_id)

    client = YomaiTestClient(app)

    # First message
    with mock_llm(["First response"]):
        await client.call("/research", "Hello", session_id=session_id)

    # Second message
    with mock_llm(["Second response"]):
        await client.call("/research", "How are you?", session_id=session_id)

    # Verify session has messages
    history = await app.memory.load(session_id)
    assert len(history) == 4  # 2 exchanges

    # First exchange
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Hello"
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "First response"


@pytest.mark.asyncio
async def test_different_sessions_independent() -> None:
    """Different session IDs maintain separate histories."""
    from typing import Any, cast

    from app.agents.researcher import app
    from yomai.testing import YomaiTestClient, mock_llm

    # Clear sessions
    await app.memory.clear("session-a")
    await app.memory.clear("session-b")

    client = YomaiTestClient(app)

    # Different conversations
    with mock_llm(["Response A"]):
        await client.call("/research", "Message A", session_id="session-a")

    with mock_llm(["Response B"]):
        await client.call("/research", "Message B", session_id="session-b")

    # Verify isolation
    history_a = await app.memory.load("session-a")
    history_b = await app.memory.load("session-b")

    assert len(history_a) == 2
    assert len(history_b) == 2
    assert history_a[1]["content"] == "Response A"
    assert history_b[1]["content"] == "Response B"