from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from yomai.jobs.models import utcnow


@dataclass(slots=True)
class StepCheckpoint:
    job_id: str
    step: str
    input_hash: str
    status: str = "succeeded"
    result: Any = None
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime = field(default_factory=utcnow)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "step": self.step,
            "input_hash": self.input_hash,
            "status": self.status,
            "result": self.result,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
        }


class CheckpointStore(Protocol):
    async def get(self, job_id: str, step: str, input_hash: str) -> StepCheckpoint | None: ...

    async def save(self, checkpoint: StepCheckpoint) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], StepCheckpoint] = {}

    async def get(self, job_id: str, step: str, input_hash: str) -> StepCheckpoint | None:
        return self._store.get((job_id, step, input_hash))

    async def save(self, checkpoint: StepCheckpoint) -> None:
        self._store[(checkpoint.job_id, checkpoint.step, checkpoint.input_hash)] = checkpoint


class RedisCheckpointStore:
    def __init__(self, url: str, *, prefix: str = "yomai", ttl_secs: int = 86400, client: Any | None = None) -> None:
        self.url = url
        self.prefix = prefix.rstrip(":")
        self.ttl_secs = ttl_secs
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from redis import asyncio as redis_asyncio  # type: ignore[import-not-found]
            except Exception as exc:  # noqa: BLE001
                from yomai.exceptions import YomaiConfigError

                raise YomaiConfigError(
                    "Redis checkpoint store requires redis to be installed.",
                    hint="Install Yomai with queue/redis extras or install redis>=5.",
                    docs="https://yomai.dev/roadmap",
                ) from exc
            self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, job_id: str, step: str, input_hash: str) -> str:
        return f"{self.prefix}:jobs:{job_id}:checkpoints:{step}:{input_hash}"

    async def get(self, job_id: str, step: str, input_hash: str) -> StepCheckpoint | None:
        raw = await self.client.get(self._key(job_id, step, input_hash))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return _checkpoint_from_dict(data)

    async def save(self, checkpoint: StepCheckpoint) -> None:
        key = self._key(checkpoint.job_id, checkpoint.step, checkpoint.input_hash)
        payload = json.dumps(checkpoint.to_dict(), separators=(",", ":"))
        if self.ttl_secs > 0:
            await self.client.set(key, payload, ex=self.ttl_secs)
        else:
            await self.client.set(key, payload)


def _dt(value: str | None) -> datetime:
    if not value:
        return utcnow()
    return datetime.fromisoformat(value)


def _checkpoint_from_dict(data: dict[str, Any]) -> StepCheckpoint:
    return StepCheckpoint(
        job_id=str(data["job_id"]),
        step=str(data["step"]),
        input_hash=str(data["input_hash"]),
        status=str(data.get("status", "succeeded")),
        result=data.get("result"),
        started_at=_dt(data.get("started_at")),
        finished_at=_dt(data.get("finished_at")),
        duration_ms=int(data.get("duration_ms", 0)),
    )
