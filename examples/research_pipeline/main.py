"""Research pipeline with human review.

Routes:
  POST /research  — Topic research → fact-check → edit → human approval → publish.

Features demonstrated:
  - Shared state (runner.state accumulates across steps)
  - runner.step() chains with retry
  - runner.branch() for quality gates
  - runner.parallel() for concurrent fact-checking
  - runner.approve() before publishing

Run:
  export ANTHROPIC_API_KEY="sk-ant-..."
  yomai run examples/research_pipeline/app.py
"""
from __future__ import annotations

import os
from typing import Literal, cast

from yomai import Yomai, tool
from yomai.config import AgentConfig, BudgetConfig, DevConfig, LLMConfig, MemoryConfig
from yomai.workflow import WorkflowRunner

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

provider = cast(Literal["anthropic", "openai"], os.environ.get("YOMAI_PROVIDER", "anthropic"))
api_key = os.environ.get("ANTHROPIC_API_KEY", "") if provider == "anthropic" else os.environ.get("OPENAI_API_KEY", "")
base_url = os.environ.get("ANTHROPIC_BASE_URL") if provider == "anthropic" else os.environ.get("OPENAI_BASE_URL")

app = Yomai(
    llm=LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=os.environ.get("YOMAI_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=1024,
    ),
    memory=MemoryConfig(backend="sqlite", db_path="research_sessions.db"),
    agent=AgentConfig(max_tool_calls=5, timeout_secs=180),
    budgets=BudgetConfig(max_tokens_per_request=8000, max_cost_per_day=1.00),
    dev=DevConfig(api_key=os.environ.get("YOMAI_API_KEY", "")),
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(cache_ttl=300)
def word_count(text: str) -> int:
    """Count the number of words in a text."""
    return len(text.split())


@tool
def check_plagiarism(text: str) -> dict[str, str]:
    """Simulated plagiarism check. In production, call a real API."""
    phrases = text.split(". ")[:3]
    return {
        "score": "low" if len(text) > 200 else "medium",
        "flagged_segments": phrases[0][:50] if len(phrases) > 0 else "none",
    }


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@app.agent("/agents/researcher")
async def researcher(message: str, session_id: str) -> None:
    pass



@app.agent("/agents/fact-checker", system="You are a fact-checker. Review the provided text for factual accuracy. Flag any claims that need verification. Use word_count to check section lengths. Output format: VERIFIED or NEEDS_REVIEW, then list issues if any.", tools=[word_count])
async def fact_checker(message: str, session_id: str) -> None:
    pass



@app.agent("/agents/editor", system="You are a senior editor. Review the draft for clarity, tone, structure, and grammar. Provide specific improvement suggestions. Output format: PASS or REVISION_NEEDED, then feedback.", tools=[word_count])
async def editor(message: str, session_id: str) -> None:
    pass



@app.agent("/agents/publisher")
async def publisher(message: str, session_id: str) -> None:
    pass



# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

@app.workflow("/research")
async def research(topic: str, runner: WorkflowRunner, depth: str = "comprehensive"):
    """Full research pipeline: research → fact-check → edit → approve → publish."""

    runner.state["topic"] = topic
    runner.state["depth"] = depth

    # Step 1: Research (with retry for transient failures)
    research_prompt = (
        f"Write a {depth} research report on: {topic}\n\n"
        "Include: Executive Summary, Background, Key Findings, Analysis, and Conclusions.\n"
        "Be thorough, cite statistics and expert opinions where applicable."
    )
    await runner.step("research", researcher, research_prompt, retries=2, backoff_secs=1.0)
    draft = runner.state["research"]
    runner.state["word_count"] = await runner.tool(word_count, text=draft)

    # Step 2: Parallel fact-checking
    fact_check_prompt = f"Fact-check this draft. Flag any claims that need verification:\n\n{draft}"
    await runner.parallel([
        runner.step("fact_check_1", fact_checker, fact_check_prompt[:3000]),
        runner.step("fact_check_2", fact_checker, fact_check_prompt[:3000]),
    ])

    # Step 3: Quality gate — branch based on fact-check results
    fc1 = runner.state.get("fact_check_1", "")
    fc2 = runner.state.get("fact_check_2", "")
    needs_revision = "NEEDS_REVIEW" in fc1 or "NEEDS_REVIEW" in fc2

    edited = await runner.branch(
        "quality_gate",
        condition=lambda s: not needs_revision,
        on_true=lambda: runner.step("editor_pass", editor,
            f"Edit this draft for publication. Current word count: {runner.state.get('word_count', 0)}:\n\n{draft}"),
        on_false=lambda: runner.step("editor_revision", editor,
            f"Revise this draft — fact-check flagged issues. Draft:\n\n{draft}\n\nFact-check feedback:\n{fc1}\n{fc2}"),
    )

    runner.state["edited"] = edited

    # Step 4: Human approval
    approval = await runner.approve(
        "Approve this research report for publication?",
        content=(
            f"Topic: {topic}\n"
            f"Words: {runner.state.get('word_count', '?')}\n"
            f"Fact-check: {'PASSED' if not needs_revision else 'NEEDS_REVISION'}\n\n"
            f"{edited[:800]}"
        ),
    )

    if approval.is_rejected:
        return {
            "status": "rejected",
            "reason": approval.comment,
            "resolved_by": approval.resolved_by,
        }

    # Step 5: Publish
    await runner.step("publish", publisher,
        f"Format for publication. Approved by {approval.resolved_by}.\n\n{edited}")

    return {
        "status": "published",
        "topic": topic,
        "word_count": runner.state.get("word_count"),
        "fact_check": "passed" if not needs_revision else "revised",
        "approved_by": approval.resolved_by,
        "content": runner.state.get("publish", "")[:200] + "...",
    }
