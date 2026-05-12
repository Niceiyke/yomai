"""Research assistant agent — searches the web, fetches URLs, and summarizes."""
from __future__ import annotations

from yomai import Depends, RouteGroup, Yomai
from yomai.config import LLMConfig, RateLimitConfig

from app.tools.search import web_search, wikipedia_lookup
from app.tools.summarize import fetch_url, summarize_text


def verify_api_key(request) -> None:
    """Simple per-route auth check."""
    from starlette.exceptions import HTTPException

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")


app = Yomai(
    llm=LLMConfig(max_tokens=512),
    rate_limits=RateLimitConfig(requests_per_minute=30),
)


# ─────────────────────────────────────────────────────────────────────────────
# Hooks
# ─────────────────────────────────────────────────────────────────────────────
@app.on("job.queued")
async def on_job_queued(event) -> None:
    """Log when a job is queued."""
    print(f"[HOOK] Job queued: {event.payload.get('job_id', 'unknown')}")


@app.on("job.succeeded")
async def on_job_done(event) -> None:
    """Log when a job completes successfully."""
    print(f"[HOOK] Job succeeded: {event.payload.get('job_id', 'unknown')}")


@app.on("job.failed")
async def on_job_failed(event) -> None:
    """Log when a job fails."""
    print(f"[HOOK] Job failed: {event.payload.get('job_id', 'unknown')}")


@app.on("stream.start")
async def on_stream_start(event) -> None:
    """Log when an agent stream starts."""
    print(f"[HOOK] Stream started for session: {event.payload.get('session_id', 'unknown')}")


# ─────────────────────────────────────────────────────────────────────────────
# Streaming Agent
# ─────────────────────────────────────────────────────────────────────────────
@app.agent(
    "/research",
    tools=[web_search, fetch_url, summarize_text, wikipedia_lookup],
    system=(
        "You are a research assistant. Use the provided tools to answer questions thoroughly.\n"
        "When a user asks about something factual, search the web first.\n"
        "If you find a relevant URL, you may fetch it to get more detail.\n"
        "Always cite your sources with the URLs you found."
    ),
    tags=["research", "v1"],
    summary="Research assistant — search, fetch, and summarize",
    cors={"allow_origins": ["http://localhost:3000"]},
)
async def research(message: str, session_id: str) -> None:
    """Main research agent — handles streaming responses."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Async Workflow
# ─────────────────────────────────────────────────────────────────────────────
@app.workflow(
    "/batch-research",
    mode="async",
    tags=["research", "workflow"],
    summary="Batch research multiple topics",
    cors={"allow_origins": ["http://localhost:3000"]},
)
async def batch_research(topics: list[str], runner) -> dict:
    """Research multiple topics in sequence and return aggregated results.

    Args:
        topics: List of search queries to research.
        runner: WorkflowRunner instance for step execution.
    """
    results = []
    for i, topic in enumerate(topics):
        # Check for cancellation between steps
        await runner.raise_if_cancelled()

        # Use the research agent as a workflow step
        step_result = await runner.step(
            name=f"research-{i}",
            agent_fn=research,
            input={"query": topic, "session_id": f"batch-{topic}"},
        )
        results.append({"topic": topic, "result": step_result})

    return {
        "count": len(results),
        "topics": [r["topic"] for r in results],
        "results": results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Session Management Endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/sessions/{session_id}",
    tags=["sessions"],
    summary="Get session message history",
    cors={"allow_origins": ["http://localhost:3000"]},
)
async def get_session(session_id: str) -> dict:
    """Return the message history for a session without calling the LLM."""
    history = await app.memory.load(session_id)
    return {
        "session_id": session_id,
        "message_count": len(history),
        "messages": history,
    }


@app.delete(
    "/sessions/{session_id}",
    tags=["sessions"],
    summary="Clear a session",
    dependencies=[Depends(verify_api_key)],
)
async def clear_session(session_id: str) -> dict:
    """Delete all messages for a session."""
    await app.memory.clear(session_id)
    return {"deleted": session_id, "message_count": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Job Status Endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/jobs/{job_id}",
    tags=["jobs"],
    summary="Get job status and metadata",
    cors={"allow_origins": ["http://localhost:3000"]},
)
async def get_job_status(job_id: str) -> dict:
    """Return the status of a background job."""
    job = await app.jobs.get(job_id)
    if job is None:
        return {"error": "Job not found", "job_id": job_id}
    return job.to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Metrics Endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/metrics",
    tags=["admin"],
    summary="Get application metrics",
)
async def get_metrics() -> dict:
    """Return basic application metrics."""
    return {
        "requests_total": app._metrics_counters["requests_total"],
        "workflow_jobs_total": app._metrics_counters["workflow_jobs_total"],
        "errors_total": app._metrics_counters["errors_total"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Versioned API Group
# ─────────────────────────────────────────────────────────────────────────────
v2 = RouteGroup("/v2", tags=["v2"], cors={"allow_origins": ["http://localhost:3000"]})


@v2.agent("/research", tools=[web_search, summarize_text])
async def research_v2(message: str, session_id: str) -> None:
    """V2 research agent — uses a different tool set."""
    pass


@v2.workflow("/batch-research", mode="async")
async def batch_research_v2(topics: list[str], runner) -> dict:
    """V2 batch research workflow."""
    results = []
    for topic in topics:
        await runner.raise_if_cancelled()
        step_result = await runner.step(
            name=f"research-{topic}",
            agent_fn=research_v2,
            input={"message": topic},
        )
        results.append(step_result)
    return {"count": len(results), "results": results}


app.include_router(v2)