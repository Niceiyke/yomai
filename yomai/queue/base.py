from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from yomai.jobs.models import JobRecord


@dataclass(frozen=True, slots=True)
class QueuedWorkflow:
    job_id: str
    route: str
    payload: dict[str, Any]
    session_id: str | None = None
    metadata: dict[str, Any] | None = None  # For flexible storage like path_kwargs


class QueueBackend(Protocol):
    async def enqueue_workflow(self, workflow: QueuedWorkflow) -> str: ...

    async def cancel(self, job_id: str) -> None: ...

    async def get_status(self, job_id: str) -> JobRecord | None: ...
