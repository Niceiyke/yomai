"""Yomai @tool functions for the support agent."""
from __future__ import annotations

from typing import Literal

from yomai import tool

from . import store


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by its ID (e.g. ORD-1001). Returns order status, product, tracking, and total."""
    order = store.lookup_order(order_id)
    if not order:
        return f"Order {order_id!r} not found."
    return (
        f"Order {order['id']}: {order['product_name']} x{order['quantity']} — "
        f"${order['total_usd']:.2f} — Status: {order['status']} — "
        f"Tracking: {order.get('tracking') or 'N/A'}"
    )


@tool
def get_customer_orders(email: str) -> str:
    """Get all orders for a customer by email address."""
    orders = store.get_orders_by_email(email)
    if not orders:
        return f"No orders found for {email}."
    lines = [f"{o['id']}: {o['product_name']} — {o['status']} — ${o['total_usd']:.2f}" for o in orders]
    return "Orders for " + email + ":\n" + "\n".join(lines)


@tool
def check_inventory(product_id: str) -> str:
    """Check current stock level for a product by its ID (e.g. PROD-501)."""
    item = store.check_inventory(product_id)
    if not item:
        return f"Product {product_id!r} not found."
    stock_label = "OUT OF STOCK" if item["stock"] == 0 else f"{item['stock']} in stock"
    return f"{item['name']} ({item['id']}) — {stock_label} — Warehouse: {item['warehouse']}"


@tool
def process_refund(order_id: str, reason: str) -> str:
    """Process a refund for an order. Requires the order ID and a reason."""
    result = store.process_refund(order_id, reason)
    if result["success"]:
        return f"Refund processed: ${result['amount']:.2f} for {result['order_id']}. Reason: {result['reason']}"
    return f"Refund failed: {result['error']}"


@tool
async def escalate_to_team(
    category: Literal["billing", "shipping", "technical", "account"],
    summary: str,
    priority: Literal["low", "medium", "high", "urgent"] = "medium",
) -> str:
    """Escalate a support ticket to the appropriate team with category, summary, and priority."""
    valid_teams = {
        "billing": "Billing Team",
        "shipping": "Logistics Team",
        "technical": "Engineering Team",
        "account": "Account Management",
    }
    team = valid_teams.get(category, "General Support")
    return f"Escalated to {team} — Priority: {priority} — Summary: {summary}"
