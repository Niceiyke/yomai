"""In-memory cache for deterministic ``@tool`` results.

Keys are built from ``(tool_name, sorted_args_json)``. Expired entries are
evicted lazily on access.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


class ToolCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def _key(self, tool_name: str, args: dict[str, Any]) -> str:
        payload = json.dumps(args, sort_keys=True, separators=(",", ":"))
        return f"{tool_name}:{hashlib.md5(payload.encode()).hexdigest()}"

    def get(self, tool_name: str, args: dict[str, Any]) -> Any | None:
        key = self._key(tool_name, args)
        entry = self._store.get(key)
        if entry is None:
            return None
        expiry, value = entry
        if expiry > 0 and time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, tool_name: str, args: dict[str, Any], value: Any, ttl_secs: int) -> None:
        key = self._key(tool_name, args)
        expiry = time.monotonic() + ttl_secs if ttl_secs > 0 else 0
        self._store[key] = (expiry, value)

    def clear(self) -> None:
        self._store.clear()


# Module-level singleton shared across all app instances in the same process
_cache = ToolCache()
