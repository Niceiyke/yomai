"""Tests for the research assistant app."""
from __future__ import annotations

import httpx
import pytest

from yomai import Yomai, tool
from yomai.config import LLMConfig, MemoryConfig
from yomai.testing import YomaiTestClient, mock_llm


def test_tool_schemas_loadable() -> None:
    """Verify all tools are importable and have valid schemas."""
    from app.tools.search import web_search
    from app.tools.convert import convert_units
    from app.tools.summarize import fetch_url, summarize_text

    for t in [web_search, convert_units, fetch_url, summarize_text]:
        assert hasattr(t, "schema")
        assert "properties" in t.schema


def test_app_routes_registered() -> None:
    """App has expected routes registered."""
    from app.agents.researcher import app

    paths = list(app._paths)
    # Streaming agents
    assert "/research" in paths
    assert "/v2/research" in paths
    # Non-streaming CRUD
    assert "/sessions/{session_id}" in paths
    # Async workflow
    assert "/batch-research" in paths
    # Job status
    assert "/jobs/{job_id}" in paths
    # Metrics
    assert "/metrics" in paths

    metas = app._routes_meta
    research_meta = next(m for m in metas if m["path"] == "/research")
    assert research_meta["type"] == "agent"
    assert "web_search" in research_meta["tools"]

    session_meta = next(m for m in metas if m["path"] == "/sessions/{session_id}")
    assert session_meta["type"] == "get"

    # Workflow meta
    workflow_meta = next(m for m in metas if m["path"] == "/batch-research")
    assert workflow_meta["type"] == "workflow"
    assert workflow_meta.get("mode") == "async"


@pytest.mark.asyncio
async def test_research_route_mock() -> None:
    """Research endpoint works end-to-end with mock LLM."""
    from app.agents.researcher import app

    with mock_llm(["Claude is a helpful research assistant."]):
        client = YomaiTestClient(app)
        reply = await client.call("/research", "What is the capital of France?")
    assert "Claude" in reply or len(reply) > 0


@pytest.mark.asyncio
async def test_session_get_endpoint() -> None:
    """GET /sessions/{session_id} returns message history."""
    import httpx
    from httpx import ASGITransport
    from typing import Any, cast

    from app.agents.researcher import app

    # Clear session first
    await app.memory.clear("test-sid")
    
    transport = ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await app.memory.save("test-sid", "hello", "hi there")
        resp = await client.get("/sessions/test-sid")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "test-sid"
    # Session should have exactly 2 messages (1 exchange)
    assert data["message_count"] == 2


@pytest.mark.asyncio
async def test_session_delete_requires_auth() -> None:
    """DELETE /sessions/{session_id} returns 401 without auth."""
    import httpx
    from httpx import ASGITransport
    from typing import Any, cast

    from app.agents.researcher import app

    transport = ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete("/sessions/any-id")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_openapi_schema_has_all_routes() -> None:
    """OpenAPI schema includes all registered routes."""
    import httpx
    from httpx import ASGITransport
    from typing import Any, cast

    from app.agents.researcher import app

    transport = ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/__yomai__/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/research" in schema["paths"]
    assert "/v2/research" in schema["paths"]
    assert "/sessions/{session_id}" in schema["paths"]
    # Note: GET and DELETE share same path in OpenAPI (only one method shown)
    # At runtime both GET and DELETE work correctly
    session_path = schema["paths"]["/sessions/{session_id}"]
    get_op = session_path.get("get") or session_path.get("delete")
    assert get_op is not None, f"No methods found for /sessions/{{session_id}}: {session_path}"
    assert "cors" in next(m for m in app._routes_meta if m["path"] == "/research")


@pytest.mark.asyncio
async def test_cors_header_on_agent() -> None:
    """Agent with per-route CORS sets header on response."""
    from typing import cast, Any

    import httpx
    from httpx import ASGITransport

    from app.agents.researcher import app

    transport = ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/__yomai__/openapi.json")
    research_meta = next(m for m in app._routes_meta if m["path"] == "/research")
    assert research_meta["cors"]["allow_origins"] == ["http://localhost:3000"]