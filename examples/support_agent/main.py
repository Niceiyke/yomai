"""Yomai Support Agent — production-ready customer support application.

Start with:
    uvicorn examples.support_agent.main:app --reload
    # or:  uv run python -m examples.support_agent
"""
from __future__ import annotations
import os
from yomai import Depends, HookEvent, Yomai, tool
from yomai.config import (
    AgentConfig,
    DevConfig,
    LLMConfig,
    MemoryConfig,
    RateLimitConfig,
    StreamingConfig,
)

from examples.support_agent import store as db
from examples.support_agent import tools
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file  

# ── Application ────────────────────────────────────────────────────────

app = Yomai(
    llm=LLMConfig(provider="anthropic", model="MiniMax-M2.7", max_tokens=1024,api_key=os.environ.get("ANTHROPIC_API_KEY", ""), base_url=os.environ.get("ANTHROPIC_BASE_URL")),
    memory=MemoryConfig(backend="sqlite", db_path="support_sessions.db", max_messages=30, ttl_hours=48),
    agent=AgentConfig(max_iterations=8, timeout_secs=180),
    streaming=StreamingConfig(heartbeat_secs=15, max_duration_secs=300),
    rate_limits=RateLimitConfig(requests_per_minute=20, max_concurrent_per_session=3),
    dev=DevConfig(ui=True, log_usage=True, reload=True),
)

SUPPORT_TOOLS = [
    tools.lookup_order,
    tools.get_customer_orders,
    tools.check_inventory,
    tools.process_refund,
    tools.escalate_to_team,
]

# ── Streaming Chat Agent ───────────────────────────────────────────────


@app.agent(
    "/chat",
    tools=SUPPORT_TOOLS,
    system=(
        "You are a helpful customer support agent for Acme Corp, an electronics retailer. "
        "Always be polite and professional. Before processing refunds, verify the order status. "
        "If a customer is frustrated, offer to escalate to the appropriate team. "
        "Use the tools available to look up orders, check inventory, and process refunds. "
        "If you cannot resolve an issue, escalate with a clear summary."
    ),
    summary="Real-time support chat with tool access",
    tags=["support", "chat"],
)
async def chat(message: str, session_id: str):
    """Streaming support chat — handle customer inquiries with tools."""
    pass


# ── Async Ticket Triage Workflow ───────────────────────────────────────


@tool
async def analyze_sentiment(text: str) -> str:
    """Analyze the sentiment of a customer message. Returns one of: positive, neutral, negative, urgent."""
    text_lower = text.lower()
    urgent_words = {"refund", "scam", "fraud", "chargeback", "lawsuit", "immediately"}
    negative_words = {"angry", "terrible", "broken", "wrong", "bad", "hate", "disappointed", "frustrated"}
    positive_words = {"thanks", "great", "love", "awesome", "helpful", "perfect", "amazing"}

    if any(w in text_lower for w in urgent_words):
        return "urgent"
    if any(w in text_lower for w in negative_words):
        return "negative"
    if any(w in text_lower for w in positive_words):
        return "positive"
    return "neutral"


@tool
async def categorize_issue(summary: str) -> str:
    """Categorize a support issue. Returns: billing, shipping, technical, or account."""
    summary_lower = summary.lower()
    if any(w in summary_lower for w in ("refund", "charge", "payment", "invoice", "billing", "price", "discount", "coupon")):
        return "billing"
    if any(w in summary_lower for w in ("shipping", "delivery", "tracking", "package", "lost", "arrived", "late")):
        return "shipping"
    if any(w in summary_lower for w in ("broken", "bug", "error", "crash", "not working", "defect", "setup", "install")):
        return "technical"
    return "account"


@app.workflow(
    "/triage",
    mode="async",
    summary="Automated ticket triage pipeline",
    tags=["support", "workflow"],
)
async def triage(message: str, runner):
    """Async workflow: analyze sentiment → categorize → create ticket → escalate."""
    ticket = db.create_ticket(runner.session_id)

    sentiment = (await analyze_sentiment(message)).strip()
    db.update_ticket(ticket.ticket_id, sentiment=sentiment)
    await runner.raise_if_cancelled()

    category = (await categorize_issue(message)).strip()
    priority = "urgent" if sentiment == "urgent" else ("high" if sentiment == "negative" else "medium")
    db.update_ticket(ticket.ticket_id, category=category, priority=priority)
    await runner.raise_if_cancelled()

    escalation = await tools.escalate_to_team(
        category=category,
        summary=message[:200],
        priority=priority,
    )
    await runner.raise_if_cancelled()

    db.update_ticket(ticket.ticket_id, routed_to=escalation, resolved=True)
    return {
        "ticket_id": ticket.ticket_id,
        "sentiment": sentiment,
        "category": category,
        "priority": priority,
        "routing": escalation,
    }


# ── REST Endpoints ─────────────────────────────────────────────────────


@app.get("/history/{session_id}", summary="Get support chat history for a session", tags=["support", "history"])
async def get_history(session_id: str):
    """Return the full conversation history for a session."""
    from yomai.memory import SqliteMemory

    mem = SqliteMemory(db_path="support_sessions.db")
    return {"session_id": session_id, "messages": await mem.load(session_id)}


@app.get("/analytics", summary="Support ticket analytics", tags=["support", "analytics"])
async def get_analytics():
    """Return aggregate ticket statistics."""
    stats = db.get_ticket_stats()
    return {"support_analytics": stats}


@app.get("/tickets/{ticket_id}", summary="Get ticket details", tags=["support", "tickets"])
async def get_ticket(ticket_id: str):
    """Return details for a specific support ticket."""
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        return {"error": f"Ticket {ticket_id!r} not found"}
    return {
        "ticket_id": ticket.ticket_id,
        "session_id": ticket.session_id,
        "sentiment": ticket.sentiment,
        "category": ticket.category,
        "priority": ticket.priority,
        "routed_to": ticket.routed_to,
        "resolved": ticket.resolved,
    }


# ── Lifecycle Hooks ────────────────────────────────────────────────────


@app.on("job.succeeded")
async def on_triage_complete(event: HookEvent):
    """Log successful triage completions."""
    print(f"[SUPPORT] Triage complete — job={event.payload.get('job_id')} "
          f"route={event.payload.get('route')}")


@app.on("error")
async def on_error(event: HookEvent):
    """Log errors for monitoring."""
    print(f"[SUPPORT] Error — job={event.payload.get('job_id')} "
          f"route={event.payload.get('route')} "
          f"error={event.payload.get('error', 'unknown')}")


@app.on("job.failed")
async def on_job_failed(event: HookEvent):
    """Alert on failed workflow jobs."""
    print(f"[SUPPORT] ALERT: Workflow job failed — job={event.payload.get('job_id')} "
          f"route={event.payload.get('route')} "
          f"error={event.payload.get('error', 'unknown')}")
