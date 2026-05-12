"""Simulated order/store database for the support agent demo."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Pre-seeded data ────────────────────────────────────────────────────

_ORDERS: dict[str, dict[str, Any]] = {
    "ORD-1001": {
        "id": "ORD-1001",
        "customer_email": "alice@example.com",
        "product_id": "PROD-501",
        "product_name": "Wireless Headphones Pro",
        "quantity": 1,
        "total_usd": 149.99,
        "status": "delivered",
        "placed_at": "2026-05-01T10:30:00Z",
        "tracking": "1Z999AA10123456784",
    },
    "ORD-1002": {
        "id": "ORD-1002",
        "customer_email": "bob@example.com",
        "product_id": "PROD-502",
        "product_name": "Mechanical Keyboard RGB",
        "quantity": 1,
        "total_usd": 89.99,
        "status": "shipped",
        "placed_at": "2026-05-08T14:15:00Z",
        "tracking": "1Z999AA10123456785",
    },
    "ORD-1003": {
        "id": "ORD-1003",
        "customer_email": "alice@example.com",
        "product_id": "PROD-503",
        "product_name": "USB-C Hub 7-in-1",
        "quantity": 2,
        "total_usd": 79.98,
        "status": "processing",
        "placed_at": "2026-05-10T09:00:00Z",
        "tracking": None,
    },
}

_INVENTORY: dict[str, dict[str, Any]] = {
    "PROD-501": {"id": "PROD-501", "name": "Wireless Headphones Pro", "stock": 42, "warehouse": "us-east-1"},
    "PROD-502": {"id": "PROD-502", "name": "Mechanical Keyboard RGB", "stock": 15, "warehouse": "us-east-1"},
    "PROD-503": {"id": "PROD-503", "name": "USB-C Hub 7-in-1", "stock": 3, "warehouse": "us-west-2"},
    "PROD-504": {"id": "PROD-504", "name": "Laptop Stand Aluminum", "stock": 0, "warehouse": "us-east-1"},
}


@dataclass
class Ticket:
    ticket_id: str
    session_id: str
    sentiment: str = ""
    category: str = ""
    priority: str = "medium"
    routed_to: str = ""
    resolved: bool = False


_tickets: dict[str, Ticket] = {}
_ticket_counter = 1000


# ── Public API ─────────────────────────────────────────────────────────


def lookup_order(order_id: str) -> dict[str, Any] | None:
    return _ORDERS.get(order_id.upper())


def get_orders_by_email(email: str) -> list[dict[str, Any]]:
    return [o for o in _ORDERS.values() if o["customer_email"] == email.lower()]


def check_inventory(product_id: str) -> dict[str, Any] | None:
    return _INVENTORY.get(product_id.upper())


def process_refund(order_id: str, reason: str) -> dict[str, Any]:
    order = _ORDERS.get(order_id.upper())
    if not order:
        return {"success": False, "error": f"Order {order_id!r} not found"}
    if order["status"] not in ("delivered", "shipped"):
        return {"success": False, "error": f"Cannot refund order with status {order['status']!r}"}
    order["status"] = "refunded"
    return {"success": True, "order_id": order_id.upper(), "amount": order["total_usd"], "reason": reason}


def create_ticket(session_id: str) -> Ticket:
    global _ticket_counter
    _ticket_counter += 1
    ticket = Ticket(ticket_id=f"TKT-{_ticket_counter}", session_id=session_id)
    _tickets[ticket.ticket_id] = ticket
    return ticket


def update_ticket(ticket_id: str, **kwargs: Any) -> Ticket | None:
    ticket = _tickets.get(ticket_id)
    if not ticket:
        return None
    for k, v in kwargs.items():
        if hasattr(ticket, k):
            setattr(ticket, k, v)
    return ticket


def get_ticket(ticket_id: str) -> Ticket | None:
    return _tickets.get(ticket_id)


def get_ticket_stats() -> dict[str, Any]:
    total = len(_tickets)
    if total == 0:
        return {"total": 0, "by_category": {}, "by_priority": {}, "resolved": 0}
    by_cat: dict[str, int] = {}
    by_pri: dict[str, int] = {}
    resolved = 0
    for t in _tickets.values():
        by_cat[t.category] = by_cat.get(t.category, 0) + 1
        by_pri[t.priority] = by_pri.get(t.priority, 0) + 1
        if t.resolved:
            resolved += 1
    return {"total": total, "by_category": by_cat, "by_priority": by_pri, "resolved": resolved}
