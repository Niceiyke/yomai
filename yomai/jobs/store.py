from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from yomai.jobs.models import JobRecord, JobStatus, utcnow


class JobStore(Protocol):
    async def create(self, record: JobRecord) -> JobRecord: ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: object = None,
        error: str | None = None,
    ) -> JobRecord | None: ...

    async def list(self) -> Iterable[JobRecord]: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    async def create(self, record: JobRecord) -> JobRecord:
        self._jobs[record.id] = record
        return record

    async def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: object = None,
        error: str | None = None,
    ) -> JobRecord | None:
        current = self._jobs.get(job_id)
        if current is None:
            return None
        updated = _updated_record(current, status, result=result, error=error)
        self._jobs[job_id] = updated
        return updated

    async def list(self) -> Iterable[JobRecord]:
        return list(self._jobs.values())


class RedisJobStore:
    """Redis-backed job store for sharing job status between web and workers."""

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
            except Exception as exc:  # noqa: BLE001 - optional dependency guard
                from yomai.exceptions import YomaiConfigError

                raise YomaiConfigError(
                    "Redis job store requires redis to be installed.",
                    hint="Install Yomai with queue/redis extras or install redis>=5.",
                    docs="https://yomai.dev/roadmap",
                ) from exc
            self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, job_id: str) -> str:
        return f"{self.prefix}:jobs:{job_id}:record"

    def _index_key(self) -> str:
        return f"{self.prefix}:jobs:index"

    async def create(self, record: JobRecord) -> JobRecord:
        key = self._key(record.id)
        idx_key = self._index_key()
        for _retry in range(3):
            pipe = self.client.pipeline(transaction=True)
            await pipe.watch(key)
            if await self.client.exists(key):
                raw = await self.client.hgetall(key)
                if raw:
                    return _record_from_redis(raw)
                continue
            pipe.multi()
            pipe.hset(key, mapping=_record_to_redis(record))
            pipe.sadd(idx_key, record.id)
            if self.ttl_secs > 0:
                pipe.expire(key, self.ttl_secs)
                pipe.expire(idx_key, self.ttl_secs)
            exec_result = await pipe.execute()
            if exec_result is not None:
                return record
        return record

    async def get(self, job_id: str) -> JobRecord | None:
        data = await self.client.hgetall(self._key(job_id))
        if not data:
            return None
        return _record_from_redis(data)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: object = None,
        error: str | None = None,
    ) -> JobRecord | None:
        key = self._key(job_id)
        for _retry in range(3):
            pipe = self.client.pipeline(transaction=True)
            await pipe.watch(key)
            raw = await self.client.hgetall(key)
            if not raw:
                await pipe.reset()
                return None
            current = _record_from_redis(raw)
            updated = _updated_record(current, status, result=result, error=error)
            data = _record_to_redis(updated)
            pipe.multi()
            pipe.hset(key, mapping=data)
            if self.ttl_secs > 0:
                pipe.expire(key, self.ttl_secs)
            exec_result = await pipe.execute()
            if exec_result is not None:
                return updated
        return None

    async def list(self) -> Iterable[JobRecord]:
        ids = await self.client.smembers(self._index_key())
        jobs: list[JobRecord] = []
        for job_id in ids:
            job = await self.get(str(job_id))
            if job is not None:
                jobs.append(job)
        return jobs

    async def _write(self, record: JobRecord) -> None:
        key = self._key(record.id)
        await self.client.hset(key, mapping=_record_to_redis(record))
        if self.ttl_secs > 0:
            await self.client.expire(key, self.ttl_secs)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _updated_record(
    current: JobRecord,
    status: JobStatus,
    *,
    result: object = None,
    error: str | None = None,
) -> JobRecord:
    now = utcnow()
    started_at = current.started_at
    finished_at = current.finished_at
    attempts = current.attempts
    if status == "running" and started_at is None:
        started_at = now
        attempts += 1
    if status in {"succeeded", "failed", "cancelled", "expired"} and finished_at is None:
        finished_at = now
    return replace(
        current,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        attempts=attempts,
        result=result if result is not None else current.result,
        error=error,
    )


def _dt_to_str(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


def _dt_from_str(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _dumps_json(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(str(value), separators=(",", ":"))


def _record_to_redis(record: JobRecord) -> dict[str, str]:
    return {
        "id": record.id,
        "route": record.route,
        "status": record.status,
        "created_at": _dt_to_str(record.created_at),
        "started_at": _dt_to_str(record.started_at),
        "finished_at": _dt_to_str(record.finished_at),
        "attempts": str(record.attempts),
        "result": _dumps_json(record.result),
        "error": record.error or "",
        "stream_url": record.stream_url or "",
        "status_url": record.status_url or "",
        "metadata": _dumps_json(record.metadata),
    }


def _loads_json(value: str, fallback: Any) -> Any:
    if value == "" or value is None:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _record_from_redis(data: dict[str, Any]) -> JobRecord:
    return JobRecord(
        id=str(data["id"]),
        route=str(data["route"]),
        status=str(data.get("status", "queued")),  # type: ignore[arg-type]
        created_at=_dt_from_str(data.get("created_at")) or utcnow(),
        started_at=_dt_from_str(data.get("started_at")),
        finished_at=_dt_from_str(data.get("finished_at")),
        attempts=int(data.get("attempts", 0)),
        result=_loads_json(data.get("result", ""), None),
        error=str(data.get("error") or "") or None,
        stream_url=str(data.get("stream_url") or "") or None,
        status_url=str(data.get("status_url") or "") or None,
        metadata=_loads_json(data.get("metadata", "{}"), {}),
    )
