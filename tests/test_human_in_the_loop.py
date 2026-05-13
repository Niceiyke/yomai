from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
import pytest

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig
from yomai.testing import MockToolCall, mock_llm
from yomai.workflow import WorkflowRunner


async def _start_workflow_and_get_interrupt(client: httpx.AsyncClient, app: Yomai,
                                            path: str, body: dict, sid: str,
                                            poll_ms: int = 50, max_wait: float = 5.0) -> str:
    """Start a workflow in the background, poll the interrupt store until an interrupt appears."""
    task = asyncio.create_task(client.post(path, json=body, headers={"X-Session-Id": sid}))
    deadline = asyncio.get_running_loop().time() + max_wait
    while asyncio.get_running_loop().time() < deadline:
        interrupts = list(app._interrupt_store._interrupts.values())
        pending = [i for i in interrupts if i.status == "pending"]
        if pending:
            return pending[0].id
        await asyncio.sleep(poll_ms / 1000)
    task.cancel()
    raise TimeoutError("No interrupt appeared")


# -------------------------------------------------------------------
# Workflow-level HITL
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_interrupt_resolves_and_task_completes() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/hitl-wf")
    async def hitl_wf(runner: WorkflowRunner):
        return await runner.interrupt("approve?")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/hitl-wf", {}, "s1")
        assert iid

        # Resolve
        r = await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "approved!"})
        assert r.status_code == 200

        # Interrupt is now resolved
        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.status == "resolved"
        assert intr.response == "approved!"


@pytest.mark.asyncio
async def test_interrupt_resume_endpoint_returns_200() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/resume-wf")
    async def resume_wf(runner: WorkflowRunner):
        return await runner.interrupt("waiting...")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/resume-wf", {}, "s2")

        resume_r = await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "done"})
        assert resume_r.status_code == 200
        assert resume_r.json()["status"] == "resolved"

        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.status == "resolved"


# -------------------------------------------------------------------
# Agent-level HITL (request_human_input tool)
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_calls_request_human_input_in_step() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/needy")
    async def needy(message: str) -> None: pass

    @app.workflow("/agent-hitl")
    async def agent_hitl(runner: WorkflowRunner):
        tc = MockToolCall("request_human_input", {"question": "Is this ok?"})
        with mock_llm([[tc], ["thanks"]]):
            return await runner.step("ask", needy, "check this")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/agent-hitl", {}, "s3")
        assert iid

        await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "yes"})

        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.response == "yes"


@pytest.mark.asyncio
async def test_agent_calls_request_human_input_in_delegate() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/helper")
    async def helper(message: str) -> None: pass

    @app.workflow("/del-hitl")
    async def del_hitl(runner: WorkflowRunner):
        tc = MockToolCall("request_human_input", {"question": "confirm?"})
        with mock_llm([[tc], ["del-result"]]):
            return await runner.delegate(helper, "task")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/del-hitl", {}, "s4")
        assert iid

        await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "confirmed"})

        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.response == "confirmed"


# -------------------------------------------------------------------
# Edge cases
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_interrupt_double_resolve_returns_404() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/once-wf")
    async def once_wf(runner: WorkflowRunner):
        return await runner.interrupt("one-time")

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/once-wf", {}, "s5")
        assert iid

        r1 = await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "first"})
        assert r1.status_code == 200

        r2 = await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "second"})
        assert r2.status_code == 404


@pytest.mark.asyncio
async def test_interrupt_bad_body_returns_400() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/__yomai__/interrupts/nonexistent/resume", json={"wrong": "field"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_interrupt_endpoint_exists() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/__yomai__/interrupts/abc123/resume", json={"response": "x"})
        assert r.status_code == 404


# -------------------------------------------------------------------
# Approval flow (structured approve/reject)
# -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approve_returns_structured_result() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/approve-wf")
    async def approve_wf(runner: WorkflowRunner):
        result = await runner.approve("Review draft", content="My draft text...")
        return {"action": result.action, "comment": result.comment, "approved": result.is_approved}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/approve-wf", {}, "s6")
        assert iid

        # Resolve with approval action
        r = await client.post(
            f"/__yomai__/interrupts/{iid}/resume",
            json={"response": "ship it", "action": "approve", "comment": "lgtm", "resolved_by": "alice"},
        )
        assert r.status_code == 200
        assert r.json()["action"] == "approve"

        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.action == "approve"
        assert intr.comment == "lgtm"
        assert intr.resolved_by == "alice"

        approval = intr.to_approval()
        assert approval.is_approved
        assert not approval.is_rejected


@pytest.mark.asyncio
async def test_approve_rejection_returns_structured_result() -> None:
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/reject-wf")
    async def reject_wf(runner: WorkflowRunner):
        result = await runner.approve("Review changes")
        return {"action": result.action, "rejected": result.is_rejected}

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/reject-wf", {}, "s7")

        r = await client.post(
            f"/__yomai__/interrupts/{iid}/resume",
            json={"response": "redo it", "action": "reject", "comment": "wrong tone"},
        )
        assert r.status_code == 200

        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.action == "reject"
        approval = intr.to_approval()
        assert approval.is_rejected
        assert not approval.is_approved


@pytest.mark.asyncio
async def test_approve_branches_on_rejection() -> None:
    """Workflow can branch based on approval result."""
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.agent("/writer")
    async def writer(message: str) -> None: pass

    @app.agent("/editor")
    async def editor(message: str) -> None: pass

    @app.workflow("/branch-approve")
    async def branch_approve(runner: WorkflowRunner):
        with mock_llm(["draft content"]):
            await runner.step("write", writer, "write a draft")

        approval = await runner.approve("Approve draft?")

        return await runner.branch(
            "approval_decision",
            condition=lambda s: approval.is_approved,
            on_true=lambda: runner.step("publish", writer, "finalize"),
            on_false=lambda: runner.step("rewrite", editor, "fix per: " + approval.comment),
        )

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/branch-approve", {}, "s8")

        # Reject it
        await client.post(
            f"/__yomai__/interrupts/{iid}/resume",
            json={"response": "no", "action": "reject", "comment": "too short"},
        )

        # Allow workflow to proceed — interrupt store shows rejection
        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        assert intr.action == "reject"
        assert intr.comment == "too short"


@pytest.mark.asyncio
async def test_interrupt_without_action_defaults_to_approved() -> None:
    """Resolving without an explicit action field treats it as approved."""
    app = Yomai(llm=LLMConfig(api_key=""), memory=MemoryConfig(backend="dict", db_path="/unused"))

    @app.workflow("/default-wf")
    async def default_wf(runner: WorkflowRunner):
        result = await runner.approve("OK?")
        return result.action

    transport = httpx.ASGITransport(app=cast(Any, app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        iid = await _start_workflow_and_get_interrupt(client, app, "/default-wf", {}, "s9")

        # Resolve without action field — defaults to approved
        await client.post(f"/__yomai__/interrupts/{iid}/resume", json={"response": "yes"})

        intr = await app._interrupt_store.get(iid)
        assert intr is not None
        approval = intr.to_approval()
        assert approval.is_approved
