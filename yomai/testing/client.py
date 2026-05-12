from __future__ import annotations

import json
from typing import Any, cast

import httpx

from yomai.core.app import Yomai


class YomaiTestClient:
    def __init__(self, app: Yomai) -> None:
        self.app = app
        self._transport = httpx.ASGITransport(app=cast(Any, app))

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, base_url="http://testserver")

    async def stream(
        self,
        path: str,
        message: str,
        session_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> list[str]:
        events = await self.get_events(path, message, session_id=session_id, extra_body=extra_body)
        return [str(event.get("content", "")) for event in events if event.get("type") == "chunk"]

    async def call(
        self,
        path: str,
        message: str,
        session_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> str:
        return "".join(await self.stream(path, message, session_id=session_id, extra_body=extra_body))

    async def get_events(
        self,
        path: str,
        message: str,
        session_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"message": message}
        if extra_body:
            body.update(extra_body)
        headers: dict[str, str] = {}
        if session_id is not None:
            headers["X-Session-Id"] = session_id

        async with await self._client() as client:
            response = await client.post(path, json=body, headers=headers)
            response.raise_for_status()
            return parse_sse(response.text)

    async def post_json(
        self,
        path: str,
        data: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send JSON POST request and return raw response."""
        async with await self._client() as client:
            return await client.post(path, json=data, headers=headers or {})


def parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_type: str | None = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if event_type == "ping":
            continue
        if not data_lines:
            continue
        data_raw = "\n".join(data_lines)
        try:
            data = json.loads(data_raw)
        except json.JSONDecodeError:
            data = {"type": event_type or "message", "data": data_raw}
        if isinstance(data, dict):
            if event_type and "event" not in data:
                data["event"] = event_type
            events.append(data)
    return events
