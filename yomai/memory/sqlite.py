from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from yomai.llm.base import Message
from yomai.memory.base import MemoryBackend


class SqliteMemory(MemoryBackend):
    """SQLite-backed session memory. Persists across process restarts.

    Schema:
        sessions(session_id TEXT PRIMARY KEY, history_json TEXT)
    """

    def __init__(self, db_path: str = "yomai_sessions.db", max_messages: int = 20) -> None:
        self._db_path = db_path
        self._max = max_messages
        self._lock = asyncio.Lock()
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "  session_id TEXT PRIMARY KEY,"
            "  history_json TEXT NOT NULL"
            ")"
        )
        conn.commit()
        conn.close()

    async def load(self, session_id: str) -> list[Message]:
        async with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT history_json FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = cur.fetchone()
            conn.close()
            if row is None:
                return []
            try:
                history: list[Message] = json.loads(row["history_json"])
            except (json.JSONDecodeError, KeyError):
                return []
            return history

    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        async with self._lock:
            history = list(await self._load_sync(session_id))
            history.append({"role": "user", "content": user_message})
            if assistant_reply:
                history.append({"role": "assistant", "content": assistant_reply})
            history = self._truncate(history)
            await self._save_sync(session_id, history)

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            await self._clear_sync(session_id)

    def _truncate(self, history: list[Message]) -> list[Message]:
        if self._max <= 0 or len(history) <= self._max:
            return history
        first = history[0]
        if first.get("role") == "system" and self._max > 1:
            return [first, *history[-(self._max - 1):]]
        return history[-self._max:]

    def _run_sync(self, fn: Any, *args: Any) -> Any:
        return fn(*args)

    async def _load_sync(self, session_id: str) -> list[Message]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._do_load, session_id)

    def _do_load(self, session_id: str) -> list[Message]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT history_json FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        conn.close()
        if row is None:
            return []
        try:
            return json.loads(row["history_json"])
        except (json.JSONDecodeError, KeyError):
            return []

    async def _save_sync(self, session_id: str, history: list[Message]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._do_save, session_id, json.dumps(history))

    def _do_save(self, session_id: str, history_json: str) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, history_json) VALUES (?, ?)",
            (session_id, history_json),
        )
        conn.commit()
        conn.close()

    async def _clear_sync(self, session_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._do_clear, session_id)

    def _do_clear(self, session_id: str) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()
