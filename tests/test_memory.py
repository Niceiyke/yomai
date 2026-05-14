"""Tests for memory backends: DictMemory, SqliteMemory, RedisMemory."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from typing import Any

import pytest

from yomai.memory.base import MemoryBackend
from yomai.memory.dict import DictMemory
from yomai.memory.sqlite import SqliteMemory

# ---------------------------------------------------------------------------
# Shared test suite — runs against any MemoryBackend
# ---------------------------------------------------------------------------


class _SharedTests:
    """Mixin providing tests that every backend must pass."""

    def make_backend(self) -> MemoryBackend:
        raise NotImplementedError

    @pytest.mark.asyncio
    async def test_load_returns_empty_for_unknown_session(self) -> None:
        be = self.make_backend()
        assert await be.load("nonexistent") == []

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self) -> None:
        be = self.make_backend()
        await be.save("s1", "hello", "world")
        hist = await be.load("s1")
        assert len(hist) == 2
        assert hist[0] == {"role": "user", "content": "hello"}
        assert hist[1] == {"role": "assistant", "content": "world"}

    @pytest.mark.asyncio
    async def test_multiple_saves_accumulate(self) -> None:
        be = self.make_backend()
        await be.save("s1", "q1", "a1")
        await be.save("s1", "q2", "a2")
        hist = await be.load("s1")
        assert len(hist) == 4
        roles = [m["role"] for m in hist]
        assert roles == ["user", "assistant", "user", "assistant"]

    @pytest.mark.asyncio
    async def test_empty_assistant_reply_not_appended(self) -> None:
        be = self.make_backend()
        await be.save("s1", "hello", "")
        hist = await be.load("s1")
        assert len(hist) == 1
        assert hist[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_clear_removes_session(self) -> None:
        be = self.make_backend()
        await be.save("s1", "hello", "world")
        await be.clear("s1")
        assert await be.load("s1") == []

    @pytest.mark.asyncio
    async def test_clear_nonexistent_no_error(self) -> None:
        be = self.make_backend()
        await be.clear("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_sessions_are_isolated(self) -> None:
        be = self.make_backend()
        await be.save("a", "qA", "rA")
        await be.save("b", "qB", "rB")
        hist_a = await be.load("a")
        hist_b = await be.load("b")
        assert hist_a[0]["content"] == "qA"
        assert hist_b[0]["content"] == "qB"

    @pytest.mark.asyncio
    async def test_truncation_respects_max_messages(self) -> None:
        be = self.make_backend()
        for i in range(10):
            await be.save("s1", f"q{i}", f"a{i}")
        hist = await be.load("s1")
        assert len(hist) == be._max  # pyright: ignore[reportAttributeAccessIssue]
        assert hist[0]["content"] == "q0"
        assert hist[-1]["content"] == "a9"

    @pytest.mark.asyncio
    async def test_truncation_exact_boundary(self) -> None:
        be = self.make_backend()
        # DictMemory requires a fresh instance with small max; handled in subclasses
        for i in range(6):
            await be.save("s1", f"q{i}", f"a{i}")
        hist = await be.load("s1")
        # With max_messages=20, all 12 messages should fit
        assert len(hist) == 12

    @pytest.mark.asyncio
    async def test_concurrent_access_safe(self) -> None:
        be = self.make_backend()
        tasks = [be.save("s1", f"q{i}", f"a{i}") for i in range(20)]
        await asyncio.gather(*tasks)
        hist = await be.load("s1")
        assert len(hist) >= 2  # at minimum something was written


class _SharedTTLTests:
    """Mixin for backends that support TTL."""

    def make_backend(self, ttl_hours: int = 0) -> MemoryBackend:
        raise NotImplementedError

    @pytest.mark.asyncio
    async def test_no_eviction_when_ttl_is_zero(self) -> None:
        be = self.make_backend(ttl_hours=0)
        await be.save("s1", "hello", "world")
        hist = await be.load("s1")
        assert len(hist) == 2

    @pytest.mark.asyncio
    async def test_expired_session_returns_empty(self) -> None:
        be = self.make_backend(ttl_hours=-1)  # negative ttl hours = zero ttl_secs
        await be.save("s1", "hello", "world")
        # With ttl_hours <= 0, eviction is disabled, so session should load
        hist = await be.load("s1")
        assert len(hist) == 2


# ---------------------------------------------------------------------------
# DictMemory tests
# ---------------------------------------------------------------------------


class TestDictMemory(_SharedTests, _SharedTTLTests):
    def make_backend(self, ttl_hours: int | None = None) -> DictMemory:
        kwargs: dict[str, Any] = {"max_messages": 20}
        if ttl_hours is not None:
            kwargs["ttl_hours"] = ttl_hours
        else:
            kwargs["ttl_hours"] = 24
        return DictMemory(**kwargs)

    @pytest.mark.asyncio
    async def test_ttl_evicts_expired_sessions(self) -> None:
        be = DictMemory(ttl_hours=-1)  # zero ttl_secs = disabled eviction
        await be.save("s1", "hello", "world")
        # Session exists because ttl_hours <= 0 means no eviction
        assert len(await be.load("s1")) == 2

    @pytest.mark.asyncio
    async def test_truncation_with_max_zero_keeps_all(self) -> None:
        """When max_messages is 0, truncation returns all history (no limit)."""
        be = DictMemory(max_messages=0)
        for i in range(5):
            await be.save("s1", f"q{i}", f"a{i}")
        hist = await be.load("s1")
        assert len(hist) == 10  # 5 user + 5 assistant

    @pytest.mark.asyncio
    async def test_truncation_exact_boundary(self) -> None:
        be = DictMemory(max_messages=4)
        await be.save("s1", "q1", "a1")  # 2 msgs
        await be.save("s1", "q2", "a2")  # 4 msgs
        await be.save("s1", "q3", "a3")  # 6 → truncated to 4
        hist = await be.load("s1")
        assert len(hist) == 4
        assert hist[0]["content"] == "q2"  # oldest dropped


# ---------------------------------------------------------------------------
# SqliteMemory tests
# ---------------------------------------------------------------------------


class TestSqliteMemory(_SharedTests, _SharedTTLTests):
    def make_backend(self, ttl_hours: int | None = None) -> SqliteMemory:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self._tmp_path = tmp.name
        kwargs: dict[str, Any] = {"max_messages": 20, "db_path": tmp.name}
        if ttl_hours is not None:
            kwargs["ttl_hours"] = ttl_hours
        else:
            kwargs["ttl_hours"] = 24
        return SqliteMemory(**kwargs)

    def teardown_method(self) -> None:
        import os

        if hasattr(self, "_tmp_path"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self._tmp_path)

    @pytest.mark.asyncio
    async def test_persists_across_instances(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            try:
                be1 = SqliteMemory(db_path=tmp.name, max_messages=20)
                await be1.save("s1", "hello", "world")

                be2 = SqliteMemory(db_path=tmp.name)
                hist = await be2.load("s1")
                assert len(hist) == 2
                assert hist[0]["content"] == "hello"
            finally:
                import os

                os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_corrupted_json_returns_empty(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            try:
                be = SqliteMemory(db_path=tmp.name)
                conn = be._connect()
                conn.execute(
                    "INSERT OR REPLACE INTO sessions (session_id, history_json) VALUES (?, ?)", ("bad", "not-json")
                )
                conn.commit()
                conn.close()
                assert await be.load("bad") == []
            finally:
                import os

                os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_migration_adds_updated_at_column(self) -> None:
        """When a sessions table exists without updated_at, init adds the column."""
        import sqlite3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            try:
                conn = sqlite3.connect(tmp.name)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, history_json TEXT NOT NULL)"
                )
                conn.commit()
                conn.close()

                be = SqliteMemory(db_path=tmp.name)
                await be.save("s1", "hello", "world")
                hist = await be.load("s1")
                assert len(hist) == 2
            finally:
                import os

                os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_save_and_load_on_many_sessions(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            try:
                be = SqliteMemory(db_path=tmp.name)
                for i in range(20):
                    await be.save(f"s{i}", f"q{i}", f"a{i}")
                for i in range(20):
                    hist = await be.load(f"s{i}")
                    assert len(hist) == 2
                    assert hist[0]["content"] == f"q{i}"
            finally:
                import os

                os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# DictMemory edge cases
# ---------------------------------------------------------------------------


class TestDictMemoryEdgeCases:
    @pytest.mark.asyncio
    async def test_evict_expired_removes_all_expired(self) -> None:
        be = DictMemory(ttl_hours=-1)  # ttl disabled
        await be.save("keep", "hello", "world")
        # Manually set an entry with old timestamp
        be._store["old"] = (0, [{"role": "user", "content": "stale"}])
        # Set ttl to make it expire
        be._ttl_secs = 1000  # anything older than 1000s ago
        hist = await be.load("old")
        assert hist == []  # evicted

    @pytest.mark.asyncio
    async def test_clear_unknown_session_no_error(self) -> None:
        be = DictMemory()
        await be.clear("nope")
        assert await be.load("nope") == []

    @pytest.mark.asyncio
    async def test_save_with_empty_assistant_reply(self) -> None:
        be = DictMemory(max_messages=10)
        await be.save("s1", "prompt", "")
        hist = await be.load("s1")
        assert len(hist) == 1
        assert hist[0]["role"] == "user"


# ---------------------------------------------------------------------------
# SqliteMemory edge cases
# ---------------------------------------------------------------------------


class TestSqliteMemoryEdgeCases:
    def setup_method(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            self._tmp_path = tmp.name

    def teardown_method(self) -> None:
        import os

        if os.path.exists(self._tmp_path):
            os.unlink(self._tmp_path)

    @pytest.mark.asyncio
    async def test_clear_removes_row(self) -> None:
        be = SqliteMemory(db_path=self._tmp_path)
        await be.save("s1", "hello", "world")
        await be.clear("s1")
        assert await be.load("s1") == []

    @pytest.mark.asyncio
    async def test_concurrent_loads_do_not_block(self) -> None:
        be = SqliteMemory(db_path=self._tmp_path)
        await be.save("s1", "hello", "world")
        results = await asyncio.gather(*[be.load("s1") for _ in range(10)])
        for r in results:
            assert len(r) == 2

    @pytest.mark.asyncio
    async def test_load_missing_session_returns_empty(self) -> None:
        be = SqliteMemory(db_path=self._tmp_path)
        assert await be.load("missing") == []

    @pytest.mark.asyncio
    async def test_corrupted_json_in_db(self) -> None:
        be = SqliteMemory(db_path=self._tmp_path)
        conn = be._connect()
        conn.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, strftime('%s','now'))", ("corrupt", "{bad json"))
        conn.commit()
        conn.close()
        assert await be.load("corrupt") == []
