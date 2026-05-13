"""Multi-agent customer support system.

Routes:
  POST /triage     — Classify and route customer queries.
  POST /escalate   — Human-in-the-loop for refunds over $500.

Features demonstrated:
  - Agent delegation (triage → billing/technical/returns)
  - Branching (route by category)
  - Tool caching (FAQ lookups, order queries)
  - HITL approvals (refund authorization)

Run:
  export ANTHROPIC_API_KEY="sk-ant-..."
  yomai run examples/customer_support/app.py
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal, cast

from yomai import Yomai, tool
from yomai.config import BudgetConfig, DevConfig, LLMConfig, MemoryConfig, RateLimitConfig
from yomai.workflow import WorkflowRunner
from dotenv import load_dotenv
load_dotenv()

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
    memory=MemoryConfig(backend="sqlite", db_path="support_sessions.db"),
    budgets=BudgetConfig(max_tokens_per_request=4000, max_cost_per_day=0.50),
    rate_limits=RateLimitConfig(requests_per_minute=30),
    dev=DevConfig(api_key=os.environ.get("YOMAI_API_KEY", "")),
)

# ---------------------------------------------------------------------------
# Tools — deterministic, cached where appropriate
# ---------------------------------------------------------------------------

# Simulated knowledge base
_KNOWLEDGE_BASE: dict[str, list[dict[str, str]]] = {
    "billing": [
        {"q": "refund policy", "a": "Refunds are processed within 5-7 business days. Items must be returned within 30 days of purchase."},
        {"q": "invoice", "a": "Invoices are emailed after each payment. You can also download them from your account → Billing → Invoices."},
        {"q": "cancel subscription", "a": "Go to Account → Subscription → Cancel. Your access continues until the end of the billing period."},
    ],
    "technical": [
        {"q": "login", "a": "Try resetting your password first. If that fails, clear your browser cache or try incognito mode."},
        {"q": "app crash", "a": "Update to the latest version (Settings → About → Check for Updates). If the issue persists, reinstall."},
        {"q": "slow loading", "a": "Check your internet connection. Try switching between WiFi and mobile data. Clear the app cache."},
    ],
    "returns": [
        {"q": "return label", "a": "Return labels are generated in Orders → Select Order → Return. Print at home or use the QR code at drop-off."},
        {"q": "return status", "a": "Track your return at Orders → Returns. Status updates within 24 hours of the carrier scanning the package."},
    ],
}

# Simulated order database
_ORDERS: dict[str, dict[str, object]] = {
    "ORD-001": {"id": "ORD-001", "customer": "alice@example.com", "total": 49.99, "status": "shipped", "items": ["Widget Pro"]},
    "ORD-002": {"id": "ORD-002", "customer": "bob@example.com", "total": 599.00, "status": "delivered", "items": ["Premium Suite"]},
}

_ticket_counter = 0


@tool(cache_ttl=3600)
def faq_lookup(category: str, question: str) -> str:
    """Search the knowledge base for a help article. Category: billing, technical, or returns."""
    articles = _KNOWLEDGE_BASE.get(category.lower(), [])
    for article in articles:
        if any(word in question.lower() for word in article["q"].split()):
            return article["a"]
    return f"No article found for '{question}' in {category}. Escalate to a human agent."


@tool(cache_ttl=30)
def get_order(order_id: str) -> dict[str, object]:
    """Look up an order by ID. Returns order details or an error."""
    order = _ORDERS.get(order_id.upper())
    if order is None:
        return {"error": f"Order {order_id} not found"}
    return order


@tool
def create_support_ticket(customer_email: str, category: str, summary: str, priority: str = "normal") -> str:
    """Create a support ticket for human follow-up."""
    global _ticket_counter
    _ticket_counter += 1
    ticket_id = f"TKT-{_ticket_counter:04d}"
    created = datetime.now(timezone.utc).isoformat()
    return (
        f"Ticket {ticket_id} created.\n"
        f"  Category: {category}\n  Priority: {priority}\n"
        f"  Customer: {customer_email}\n  Summary: {summary}\n"
        f"  Created: {created}"
    )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@app.agent("/specialist/billing", system="You are a billing support specialist. Help with invoices, payments, refunds, and subscriptions. Use faq_lookup first. If the customer's issue is resolved, summarize. If the customer needs a refund over $100, create a support ticket for human review. Be concise and friendly.", tools=[faq_lookup, get_order, create_support_ticket])
async def billing_specialist(message: str, session_id: str) -> None:
    # Handler runs BEFORE the LLM. Use it for:
    #   1. Validation — block abusive/spam messages
    #   2. Pre-processing — inject customer context
    #   3. Authorization — check rate limits, permissions
    #   4. Side effects — log, audit, increment counters
    if not message or len(message) < 3:
        raise ValueError("Message too short")
    # The LLM runs automatically after this function returns


@app.agent("/specialist/technical", system="You are a technical support specialist. Help with login issues, app crashes, bugs, and errors. Use faq_lookup with category='technical' for known solutions. If you cannot resolve the issue, recommend creating a support ticket. Be concise and helpful.", tools=[faq_lookup])
async def technical_specialist(message: str, session_id: str) -> None:
    pass


@app.agent("/specialist/returns", system="You are a returns specialist. Help with return labels, return status, and exchange policies. Use faq_lookup with category='returns' and get_order to check order details. Be empathetic and efficient.", tools=[faq_lookup, get_order])
async def returns_specialist(message: str, session_id: str) -> None:
    pass



# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

@app.workflow("/triage")
async def triage(email: str, query: str, runner: WorkflowRunner):
    """Classify and route a customer query to the right specialist."""
    runner.state["customer_email"] = email
    runner.state["original_query"] = query

    # Step 1: Classify the query
    classification_prompt = (
        f"Classify this customer query into exactly one category: billing, technical, or returns.\n"
        f"Customer: {email}\nQuery: {query}\n\n"
        f"Reply with ONLY the category name (lowercase)."
    )
    category = await runner.step("classify", billing_specialist, classification_prompt)
    runner.state["category"] = category.strip().lower()

    # Step 2: Branch to the right specialist
    answer = await runner.branch(
        "route",
        condition=lambda s: s.get("category", "") in ("billing", "technical", "returns"),
        on_true=lambda: _route_to_specialist(runner),
        on_false=lambda: runner.step("unknown", billing_specialist,
            f"Handle this unknown-category query directly: {query}"),
    )
    runner.state["answer"] = answer

    # Step 3: Check if escalation needed
    needs_escalation = "refund" in query.lower() and "500" in query or "refund" in query.lower() and "599" in query

    if needs_escalation:
        ticket = await runner.tool(create_support_ticket,
            customer_email=email, category=runner.state["category"],
            summary=query, priority="high")
        runner.state["ticket"] = ticket

    return {
        "category": runner.state.get("category"),
        "answer": answer,
        "ticket": runner.state.get("ticket"),
        "escalated": needs_escalation,
    }


async def _route_to_specialist(runner: WorkflowRunner) -> str:
    """Route to the correct specialist agent using delegation."""
    category = runner.state.get("category", "general")
    query = runner.state.get("original_query", "")

    specialist_map = {
        "billing": billing_specialist,
        "technical": technical_specialist,
        "returns": returns_specialist,
    }
    agent_fn = specialist_map.get(category, billing_specialist)
    return await runner.delegate(agent_fn, query)


@app.workflow("/escalate")
async def escalate(order_id: str, reason: str, runner: WorkflowRunner):
    """Human-in-the-loop escalation for high-value refunds."""
    # Step 1: Look up the order
    order = await runner.tool(get_order, order_id=order_id)
    runner.state["order"] = order

    if isinstance(order, dict) and "error" in order:
        return {"error": order["error"]}

    # Step 2: Human approval gate
    total = order.get("total", 0)
    approval = await runner.approve(
        f"Refund request for ${total} — approve?",
        content=(
            f"Order: {order_id}\n"
            f"Customer: {order.get('customer')}\n"
            f"Amount: ${total}\n"
            f"Items: {order.get('items')}\n"
            f"Reason: {reason}"
        ),
    )

    # Step 3: Process or reject
    if approval.is_approved:
        ticket = await runner.tool(create_support_ticket,
            customer_email=str(order.get("customer", "")),
            category="billing", summary=f"Refund {order_id}: {reason}",
            priority="urgent")
        return {"status": "approved", "ticket": ticket, "comment": approval.comment}
    else:
        return {"status": "rejected", "comment": approval.comment}
