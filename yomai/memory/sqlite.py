from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sqlite3

from yomai.llm.base import Message
from yomai.memory.base import MemoryBackend


class SqliteMemory(MemoryBackend):
    """SQLite-backed session memory. Persists across process restarts."""

    def __init__(self, db_path: str = "yomai_sessions.db", max_messages: int = 20, ttl_hours: int = 24) -> None:
        self._db_path = db_path
        self._max = max_messages
        self._ttl_secs = max(0, ttl_hours) * 3600
        self._lock = asyncio.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="yomai-sqlite")
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "  session_id TEXT PRIMARY KEY,"
            "  history_json TEXT NOT NULL,"
            "  updated_at REAL NOT NULL DEFAULT (strftime('%s','now'))"
            ")"
        )
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")
            conn.execute("UPDATE sessions SET updated_at = strftime('%s','now') WHERE updated_at = 0")
        conn.commit()
        conn.close()

    async def load(self, session_id: str) -> list[Message]:
        async with self._lock:
            await self._delete_expired_sync()
            return await self._load_sync(session_id)

    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        async with self._lock:
            await self._delete_expired_sync()
            history = list(await self._load_sync(session_id))
            history.append({"role": "user", "content": user_message})
            if assistant_reply:
                history.append({"role": "assistant", "content": assistant_reply})
            await self._save_sync(session_id, self._truncate(history))

    async def clear(self, session_id: str) -> None:
        async with self._lock:
            await self._clear_sync(session_id)

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            self._executor.shutdown(wait=False)

    def _truncate(self, history: list[Message]) -> list[Message]:
        if self._max <= 0 or len(history) <= self._max:
            return history
        first = history[0]
        if first.get("role") == "system" and self._max > 1:
            return [first, *history[-(self._max - 1):]]
        return history[-self._max:]

    async def _load_sync(self, session_id: str) -> list[Message]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._do_load, session_id)

    def _do_load(self, session_id: str) -> list[Message]:
        conn = self._get_conn()
        cur = conn.execute("SELECT history_json FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        if row is None:
            return []
        try:
            return json.loads(row["history_json"])
        except (json.JSONDecodeError, KeyError):
            return []

    async def _save_sync(self, session_id: str, history: list[Message]) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._do_save, session_id, json.dumps(history))

    def _do_save(self, session_id: str, history_json: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO sessions (session_id, history_json, updated_at) VALUES (?, ?, strftime('%s','now'))",
            (session_id, history_json),
        )
        conn.commit()

    async def _clear_sync(self, session_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._do_clear, session_id)

    def _do_clear(self, session_id: str) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()

    async def _delete_expired_sync(self) -> None:
        if self._ttl_secs <= 0:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._do_delete_expired)

    def _do_delete_expired(self) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM sessions WHERE updated_at < strftime('%s','now') - ?", (self._ttl_secs,))
        conn.commit()
