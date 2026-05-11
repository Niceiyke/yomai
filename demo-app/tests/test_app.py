"""Tests for the research assistant app."""
from __future__ import annotations

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

    # Agent route
    assert "/research" in app._paths

    # Routes metadata has them
    metas = app._routes_meta
    research_meta = next(m for m in metas if m["path"] == "/research")
    assert research_meta["type"] == "agent"
    assert "web_search" in research_meta["tools"]


@pytest.mark.asyncio
async def test_research_route_mock() -> None:
    """Research endpoint works end-to-end with mock LLM."""
    from app.agents.researcher import app

    with mock_llm(["Claude is a helpful research assistant."]):
        client = YomaiTestClient(app)
        reply = await client.call("/research", "What is the capital of France?")
    assert "Claude" in reply or len(reply) > 0


@pytest.mark.asyncio
async def test_openapi_schema_has_all_routes() -> None:
    """OpenAPI schema includes all registered routes."""
    from app.agents.researcher import app

    from starlette.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/__yomai__/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/research" in schema["paths"]