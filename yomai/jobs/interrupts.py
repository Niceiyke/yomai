from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResumeRequest(BaseModel):
    """Request body for ``POST /__yomai__/interrupts/{id}/resume``."""

    response: str = Field(..., min_length=1, description="Human's response to the interrupt")
    action: str | None = Field(default=None, pattern="^(approve|reject)?$",
                               description="Structured approval action")
    comment: str = Field(default="", description="Optional feedback from the reviewer")
    resolved_by: str = Field(default="", description="Identifier of the human resolver")


@dataclass(slots=True)
class ApprovalResult:
    """The outcome of a human approval request."""

    action: str  # "approved" | "rejected"
    comment: str = ""
    resolved_by: str = ""
    resolved_at: datetime = field(default_factory=_utcnow)

    @property
    def is_approved(self) -> bool:
        return self.action in ("approved", "approve")

    @property
    def is_rejected(self) -> bool:
        return self.action in ("rejected", "reject")


@dataclass(slots=True)
class Interrupt:
    id: str
    job_id: str
    message: str
    status: str = "pending"  # pending | resolved | timeout
    response: str | None = None
    action: str | None = None       # "approved" | "rejected" | None (free-text)
    comment: str = ""
    resolved_by: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    resolved_at: datetime | None = None

    def to_approval(self) -> ApprovalResult:
        """Extract an ApprovalResult from this interrupt."""
        return ApprovalResult(
            action=self.action or "approved",
            comment=self.comment,
            resolved_by=self.resolved_by,
            resolved_at=self.resolved_at or _utcnow(),
        )


class InterruptStore(Protocol):
    async def create(self, interrupt: Interrupt) -> None: ...
    async def get(self, interrupt_id: str) -> Interrupt | None: ...
    async def resolve(self, interrupt_id: str, response: str, *, action: str | None = None,
                      comment: str = "", resolved_by: str = "") -> bool: ...
    async def delete(self, interrupt_id: str) -> None: ...


class InMemoryInterruptStore:
    def __init__(self) -> None:
        self._interrupts: dict[str, Interrupt] = {}
        self._events: dict[str, asyncio.Event] = {}

    async def create(self, interrupt: Interrupt) -> None:
        self._interrupts[interrupt.id] = interrupt

    async def get(self, interrupt_id: str) -> Interrupt | None:
        return self._interrupts.get(interrupt_id)

    async def resolve(
        self,
        interrupt_id: str,
        response: str,
        *,
        action: str | None = None,
        comment: str = "",
        resolved_by: str = "",
    ) -> bool:
        interrupt = self._interrupts.get(interrupt_id)
        if interrupt is None or interrupt.status != "pending":
            return False
        interrupt.status = "resolved"
        interrupt.response = response
        interrupt.action = action
        interrupt.comment = comment
        interrupt.resolved_by = resolved_by
        interrupt.resolved_at = _utcnow()
        event = self._events.pop(interrupt_id, None)
        if event:
            event.set()
        return True

    def event(self, interrupt_id: str) -> asyncio.Event:
        if interrupt_id not in self._events:
            self._events[interrupt_id] = asyncio.Event()
        return self._events[interrupt_id]

    async def delete(self, interrupt_id: str) -> None:
        self._interrupts.pop(interrupt_id, None)
        self._events.pop(interrupt_id, None)
