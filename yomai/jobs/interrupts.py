from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

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

    async def get_latest_resolved(self) -> Interrupt | None:
        resolved = [i for i in self._interrupts.values() if i.status == "resolved" and i.response is not None]
        if resolved:
            return max(resolved, key=lambda i: i.resolved_at or datetime.min.replace(tzinfo=timezone.utc))
        return None


class RedisInterruptStore:
    """Redis-backed interrupt store for multi-worker deployments."""

    def __init__(self, url: str, *, prefix: str = "yomai", ttl_secs: int = 3600, client: Any | None = None) -> None:
        self.url = url
        self.prefix = prefix.rstrip(":")
        self.ttl_secs = ttl_secs
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
            except Exception as exc:
                from yomai.exceptions import YomaiConfigError
                raise YomaiConfigError(
                    "Redis interrupt store requires redis to be installed.",
                    hint="Install Yomai with redis extras.",
                ) from exc
            self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, interrupt_id: str) -> str:
        return f"{self.prefix}:interrupts:{interrupt_id}"

    async def create(self, interrupt: Interrupt) -> None:
        key = self._key(interrupt.id)
        data = json.dumps({"id": interrupt.id, "job_id": interrupt.job_id, "message": interrupt.message,
                           "status": "pending", "created_at": interrupt.created_at.isoformat()})
        for _retry in range(3):
            await self.client.watch(key)
            if await self.client.exists(key):
                await self.client.unwatch()
                return
            tr = self.client.multi()
            tr.set(key, data, ex=self.ttl_secs)
            exec_result = await tr.execute()
            if exec_result is not None:
                return

    async def get(self, interrupt_id: str) -> Interrupt | None:
        raw = await self.client.get(self._key(interrupt_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return Interrupt(id=data["id"], job_id=data["job_id"], message=data["message"],
                         status=data.get("status", "pending"), response=data.get("response"),
                         action=data.get("action"), comment=data.get("comment", ""),
                         resolved_by=data.get("resolved_by", ""))

    async def resolve(self, interrupt_id: str, response: str, *, action: str | None = None,
                      comment: str = "", resolved_by: str = "") -> bool:
        key = self._key(interrupt_id)
        for _retry in range(3):
            await self.client.watch(key)
            raw = await self.client.get(key)
            if not raw:
                await self.client.unwatch()
                return False
            try:
                current_data = json.loads(raw)
            except json.JSONDecodeError:
                await self.client.unwatch()
                return False
            if current_data.get("status") != "pending":
                await self.client.unwatch()
                return False
            new_data = json.dumps({
                "id": interrupt_id, "job_id": current_data.get("job_id", ""),
                "message": current_data.get("message", ""),
                "status": "resolved", "response": response, "action": action,
                "comment": comment, "resolved_by": resolved_by,
                "created_at": current_data.get("created_at", datetime.now(timezone.utc).isoformat()),
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            })
            tr = self.client.multi()
            tr.set(key, new_data, ex=self.ttl_secs)
            exec_result = await tr.execute()
            if exec_result is not None:
                return True
        return False

    async def delete(self, interrupt_id: str) -> None:
        await self.client.delete(self._key(interrupt_id))

    def event(self, interrupt_id: str) -> asyncio.Event:
        """Return a local Event for waiting (poll-based for Redis). The runner
        polls get() every 200ms until the interrupt is resolved."""
        return asyncio.Event()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
