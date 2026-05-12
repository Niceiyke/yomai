"""Integration tests for the Support Agent demo app."""
from __future__ import annotations

import asyncio

import pytest

from yomai.testing import MockToolCall, YomaiTestClient, mock_llm

from examples.support_agent.main import app


@pytest.mark.asyncio
async def test_chat_streams_with_tool_calls() -> None:
    """Agent chat streams text chunks and invokes lookup_order tool."""
    tool_call = MockToolCall("lookup_order", {"order_id": "ORD-1001"})
    with mock_llm([["Let me look that up for you.", tool_call, "Your order was delivered."]]):
        events = await YomaiTestClient(app).get_events("/chat", "Where is my order ORD-1001?")

    chunks = [e["content"] for e in events if e.get("type") == "chunk"]
    tool_starts = [e for e in events if e.get("type") == "tool_start"]

    assert "Let me look that up" in "".join(chunks)
    assert "delivered" in "".join(chunks).lower()
    assert len(tool_starts) >= 1
    assert tool_starts[0]["name"] == "lookup_order"


@pytest.mark.asyncio
async def test_chat_processes_refund() -> None:
    """Agent processes a refund via the process_refund tool."""
    tool_call = MockToolCall("process_refund", {"order_id": "ORD-1001", "reason": "defective item"})
    with mock_llm([["Absolutely, let me process that refund.", tool_call, "Refund complete!"]]):
        events = await YomaiTestClient(app).get_events("/chat", "I need a refund for ORD-1001")

    tool_ends = [e for e in events if e.get("type") == "tool_end" and e.get("name") is None]
    tool_results = [e["result"] for e in tool_ends]
    assert len(tool_results) >= 1
    assert "Refund processed" in tool_results[0]


@pytest.mark.asyncio
async def test_chat_checks_inventory() -> None:
    """Agent checks inventory via check_inventory tool."""
    tool_call = MockToolCall("check_inventory", {"product_id": "PROD-504"})
    with mock_llm([["Let me check stock for you.", tool_call, "That item is out of stock."]]):
        events = await YomaiTestClient(app).get_events("/chat", "Is PROD-504 available?")

    tool_ends = [e for e in events if e.get("type") == "tool_end" and e.get("name") is None]
    tool_results = [e["result"] for e in tool_ends]
    assert len(tool_results) >= 1
    assert "OUT OF STOCK" in tool_results[0]


@pytest.mark.asyncio
async def test_chat_handles_unknown_order() -> None:
    """Agent handles lookup of non-existent order gracefully."""
    tool_call = MockToolCall("lookup_order", {"order_id": "ORD-9999"})
    with mock_llm([["Let me check.", tool_call, "I couldn't find that order."]]):
        events = await YomaiTestClient(app).get_events("/chat", "Where is ORD-9999?")

    tool_ends = [e for e in events if e.get("type") == "tool_end" and e.get("name") is None]
    tool_results = [e["result"] for e in tool_ends]
    assert len(tool_results) >= 1
    assert "not found" in tool_results[0].lower()


@pytest.mark.asyncio
async def test_triage_workflow_completes() -> None:
    """Async ticket triage workflow creates a job and returns 202."""
    client = YomaiTestClient(app)
    resp = await client.post_json("/triage", {
        "message": "I'm very frustrated — my order is broken and I want a refund immediately",
    })

    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert "stream_url" in data
    assert "/__yomai__/jobs/" in data["status_url"]


@pytest.mark.asyncio
async def test_triage_and_ticket_lifecycle() -> None:
    """Full lifecycle: triage workflow creates ticket, then GET /tickets/{id} retrieves it."""
    client = YomaiTestClient(app)
    resp = await client.post_json("/triage", {
        "message": "My package is late and I'm angry",
    })
    data = resp.json()
    job_id = data["job_id"]

    # Poll until the job completes
    for _ in range(30):
        async with await client._client() as http:
            status_resp = await http.get(f"/__yomai__/jobs/{job_id}")
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                if status_data.get("status") in ("succeeded", "failed"):
                    break
        await asyncio.sleep(0.1)

    # Job should have result with ticket info
    async with await client._client() as http:
        status_resp = await http.get(f"/__yomai__/jobs/{job_id}")
        job = status_resp.json()
        result = job.get("result", {})
        ticket_id = result.get("ticket_id")
        assert ticket_id is not None

        # Verify ticket via GET endpoint
        ticket_resp = await http.get(f"/tickets/{ticket_id}")
        assert ticket_resp.status_code == 200
        ticket = ticket_resp.json()
        assert ticket["sentiment"] == "negative"
        assert ticket["category"] == "shipping"
        assert ticket["priority"] == "high"
