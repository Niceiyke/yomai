"""In-memory cache for deterministic ``@tool`` results.

Keys are built from ``(tool_name, sorted_args_json)``. Expired entries are
evicted lazily on access. Oldest entries are evicted when the cache exceeds
``maxsize``.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any


class ToolCache:
    DEFAULT_MAXSIZE = 10_000

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._maxsize = max(maxsize, 1)

    @staticmethod
    def _key(tool_name: str, args: dict[str, Any]) -> str:
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
        self._store.move_to_end(key)
        return value

    def set(self, tool_name: str, args: dict[str, Any], value: Any, ttl_secs: int) -> None:
        key = self._key(tool_name, args)
        expiry = time.monotonic() + ttl_secs if ttl_secs > 0 else 0
        if key in self._store:
            self._store.move_to_end(key)
        else:
            while len(self._store) >= self._maxsize:
                self._store.popitem(last=False)
        self._store[key] = (expiry, value)

    def clear(self) -> None:
        self._store.clear()
