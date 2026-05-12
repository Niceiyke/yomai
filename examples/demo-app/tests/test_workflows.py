"""Tests for async workflows in the research assistant app."""
from __future__ import annotations

import httpx
import pytest

from yomai.testing import YomaiTestClient, mock_llm


@pytest.mark.asyncio
async def test_batch_research_returns_job_id() -> None:
    """POST /batch-research returns 202 with job_id."""
    from typing import Any, cast

    from app.agents.researcher import app

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/batch-research",
            json={"topics": ["AI safety", "machine learning"]},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["job_id"].startswith("job_")


@pytest.mark.asyncio
async def test_batch_research_creates_job_record() -> None:
    """Batch research creates a job record that can be queried."""
    from typing import Any, cast

    from app.agents.researcher import app

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create job
        resp = await client.post(
            "/batch-research",
            json={"topics": ["climate change"]},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Check job status
        status_resp = await client.get(f"/jobs/{job_id}")
        assert status_resp.status_code == 200
        job_data = status_resp.json()
        assert job_data["id"] == job_id
        assert job_data["route"] == "/batch-research"


@pytest.mark.asyncio
async def test_job_status_not_found() -> None:
    """GET /jobs/{job_id} returns error for unknown job."""
    from typing import Any, cast

    from app.agents.researcher import app

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/jobs/nonexistent-job-id")
    assert resp.status_code == 200  # Route exists, returns error dict
    data = resp.json()
    assert "error" in data
    assert data["error"] == "Job not found"


@pytest.mark.asyncio
async def test_metrics_endpoint() -> None:
    """GET /metrics returns application metrics."""
    from typing import Any, cast

    from app.agents.researcher import app

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert "requests_total" in data
    assert "workflow_jobs_total" in data
    assert "errors_total" in data
    # All counters should be integers
    assert isinstance(data["requests_total"], int)


@pytest.mark.asyncio
async def test_v2_batch_workflow() -> None:
    """V2 has its own batch workflow endpoint."""
    from typing import Any, cast

    from app.agents.researcher import app

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v2/batch-research",
            json={"topics": ["test"]},
        )

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data


@pytest.mark.asyncio
async def test_workflow_openapi_schema() -> None:
    """OpenAPI schema documents workflows correctly."""
    from typing import Any, cast

    from app.agents.researcher import app

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/__yomai__/openapi.json")

    schema = resp.json()

    # Check batch-research workflow
    assert "/batch-research" in schema["paths"]
    batch_path = schema["paths"]["/batch-research"]
    post_op = batch_path.get("post")
    assert post_op is not None, f"POST method not found for /batch-research: {batch_path}"
    assert post_op.get("x-yomai-type") == "workflow"
    # Note: list type inference for request body params needs improvement in V2

    # Check job status endpoint
    assert "/jobs/{job_id}" in schema["paths"]
    get_op = schema["paths"]["/jobs/{job_id}"]["get"]
    assert get_op["summary"] == "Get job status and metadata"

    # Check metrics endpoint
    assert "/metrics" in schema["paths"]
    assert schema["paths"]["/metrics"]["get"]["summary"] == "Get application metrics"