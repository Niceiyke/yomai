from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

JobStatus = Literal[
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class JobRecord:
    id: str
    route: str
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    result: Any = None
    error: str | None = None
    stream_url: str | None = None
    status_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "route": self.route,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
            "attempts": self.attempts,
            "result": self.result,
            "error": self.error,
            "stream_url": self.stream_url,
            "status_url": self.status_url,
            "metadata": self.metadata,
        }
