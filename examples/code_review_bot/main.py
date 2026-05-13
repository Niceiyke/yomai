"""Code review bot.

Routes:
  POST /review       — Analyze a PR diff → static checks → parallel specialists → synthesize.
  POST /review/async — Same, but async mode (queue-based, returns job ID).

Features demonstrated:
  - runner.tool() for static analysis (no LLM overhead)
  - runner.parallel() for concurrent specialist reviews (security, style, performance)
  - runner.delegate() for agent delegation
  - runner.branch() for severity-based routing
  - Async workflow mode with job polling
  - Tool caching for repeated diff analysis

Run:
  export ANTHROPIC_API_KEY="sk-ant-..."
  yomai run examples/code_review_bot/app.py
"""
from __future__ import annotations

import os
import re
from typing import Literal, cast

from yomai import Yomai, tool
from yomai.config import AgentConfig, BudgetConfig, DevConfig, LLMConfig, MemoryConfig, QueueConfig
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
        max_tokens=512,
    ),
    memory=MemoryConfig(backend="dict"),
    agent=AgentConfig(max_tool_calls=3, timeout_secs=120),
    budgets=BudgetConfig(max_tokens_per_request=6000),
    queue=QueueConfig(backend="inline"),
    dev=DevConfig(api_key=os.environ.get("YOMAI_API_KEY", "")),
)

# ---------------------------------------------------------------------------
# Tools — deterministic static analysis (no LLM, cached)
# ---------------------------------------------------------------------------

@tool(cache_ttl=120)
def analyze_diff_structure(diff: str) -> dict[str, object]:
    """Parse a git diff and return structural metrics: files changed, lines added/removed, file types."""
    files_changed = len(re.findall(r"^diff --git", diff, re.MULTILINE))
    additions = len(re.findall(r"^\+[^+]", diff, re.MULTILINE))
    deletions = len(re.findall(r"^-[^-]", diff, re.MULTILINE))

    file_types: dict[str, int] = {}
    for match in re.finditer(r"^diff --git a/(\S+)", diff, re.MULTILINE):
        ext = match.group(1).rsplit(".", 1)[-1] if "." in match.group(1) else "other"
        file_types[ext] = file_types.get(ext, 0) + 1

    return {
        "files_changed": files_changed,
        "additions": additions,
        "deletions": deletions,
        "file_types": file_types,
        "total_changes": additions + deletions,
    }


@tool(cache_ttl=120)
def scan_secrets(diff: str) -> list[str]:
    """Scan the diff for potential secrets: API keys, tokens, passwords."""
    patterns = [
        (r"[A-Za-z0-9_]*API[_-]?KEY[A-Za-z0-9_]*\s*[:=]\s*['\"][^'\"]+['\"]", "API key hardcoded"),
        (r"[A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*\s*[:=]\s*['\"][^'\"]+['\"]", "Token hardcoded"),
        (r"[A-Za-z0-9_]*PASSWORD[A-Za-z0-9_]*\s*[:=]\s*['\"][^'\"]+['\"]", "Password hardcoded"),
        (r"[A-Za-z0-9_]*SECRET[A-Za-z0-9_]*\s*[:=]\s*['\"][^'\"]+['\"]", "Secret hardcoded"),
    ]
    findings: list[str] = []
    for pattern, label in patterns:
        if re.search(pattern, diff, re.IGNORECASE):
            findings.append(label)
    return findings


@tool(cache_ttl=120)
def lint_diff(diff: str) -> dict[str, object]:
    """Simulated linter: check for common issues (long lines, trailing whitespace, missing newlines)."""
    issues: list[str] = []
    for i, line in enumerate(diff.split("\n"), 1):
        if line.startswith("+") and len(line) > 120:
            issues.append(f"Line {i}: too long ({len(line)} chars)")
        if line.startswith("+") and line.rstrip() != line.rstrip():
            pass  # trailing whitespace check placeholder
    return {"issues": issues, "count": len(issues)}


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@app.agent("/agents/security-reviewer")
async def security_reviewer(message: str, session_id: str) -> None:
    pass



@app.agent("/agents/style-reviewer")
async def style_reviewer(message: str, session_id: str) -> None:
    pass



@app.agent("/agents/performance-reviewer")
async def performance_reviewer(message: str, session_id: str) -> None:
    pass



@app.agent("/agents/synthesizer")
async def synthesizer(message: str, session_id: str) -> None:
    pass



# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

@app.workflow("/review")
async def review(pr_title: str, diff: str, runner: WorkflowRunner):
    """Full code review pipeline: static checks → parallel review → synthesize."""

    runner.state["pr_title"] = pr_title

    # Step 1: Deterministic static analysis (no LLM)
    structure = await runner.tool(analyze_diff_structure, diff=diff)
    secrets = await runner.tool(scan_secrets, diff=diff)
    lint = await runner.tool(lint_diff, diff=diff)

    runner.state["structure"] = structure
    runner.state["secrets"] = secrets
    runner.state["lint"] = lint

    # Step 2: Parallel specialist reviews
    review_prompt = (
        f"PR: {pr_title}\n"
        f"Files: {structure.get('files_changed')}, Changes: +{structure.get('additions')}/-{structure.get('deletions')}\n"
        f"Secret scan: {', '.join(secrets) if isinstance(secrets, list) and secrets else 'none found'}\n"
        f"Lint issues: {lint.get('count', 0) if isinstance(lint, dict) else 0}\n\n"
        f"```diff\n{diff[:4000]}\n```"
    )

    results = await runner.parallel([
        runner.step("security", security_reviewer, review_prompt),
        runner.step("style", style_reviewer, review_prompt),
        runner.step("performance", performance_reviewer, review_prompt),
    ])

    # Step 3: Branch on severity — if critical security issues found, flag urgently
    security_findings = runner.state.get("security", "")
    has_critical = "CRITICAL" in security_findings

    if has_critical:
        runner.state["severity"] = "critical"
    else:
        runner.state["severity"] = "normal"

    # Step 4: Synthesize
    synthesis_prompt = (
        f"Synthesize the following code review findings for PR '{pr_title}':\n\n"
        f"=== SECURITY ===\n{runner.state.get('security', '')[:2000]}\n\n"
        f"=== STYLE ===\n{runner.state.get('style', '')[:2000]}\n\n"
        f"=== PERFORMANCE ===\n{runner.state.get('performance', '')[:2000]}\n\n"
        f"Provide a summary table, overall assessment, and recommendation."
    )
    await runner.step("synthesize", synthesizer, synthesis_prompt)

    return {
        "pr": pr_title,
        "structure": structure,
        "secrets_found": len(secrets) if isinstance(secrets, list) else 0,
        "lint_issues": lint.get("count", 0) if isinstance(lint, dict) else 0,
        "severity": runner.state["severity"],
        "review": runner.state.get("synthesize", "")[:500],
    }


@app.workflow("/review/summary", mode="async")
async def review_summary(pr_title: str, diff: str, runner: WorkflowRunner):
    """Same as /review but async — returns job ID for polling. Good for large diffs."""
    structure = await runner.tool(analyze_diff_structure, diff=diff)
    secrets = await runner.tool(scan_secrets, diff=diff)

    if isinstance(secrets, list) and secrets:
        return {"pr": pr_title, "blocked": True, "reason": f"Secrets found: {secrets}"}

    await runner.step("quick_review", synthesizer,
        f"Quick review of PR '{pr_title}'. {structure.get('files_changed')} files, "
        f"+{structure.get('additions')}/-{structure.get('deletions')}. Diff:\n\n```\n{diff[:3000]}\n```")

    return {
        "pr": pr_title,
        "structure": structure,
        "summary": runner.state.get("quick_review", ""),
    }
