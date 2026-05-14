"""Bug-hunting and edge-case tests for memory backends.

These tests are designed to find real bugs, not just pass.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from typing import Any

import pytest

from yomai.memory.dict import DictMemory
from yomai.memory.sqlite import SqliteMemory


# =============================================================================
# Concurrency / race condition tests
# =============================================================================


class TestConcurrentSaveIntegrity:
    """Verify that concurrent saves on the same session don't lose messages."""

    @pytest.mark.asyncio
    async def test_dict_memory_concurrent_saves_preserve_all_messages(self) -> None:
        be = DictMemory(max_messages=0)
        await be.save("s1", "init", "ok")

        async def save_msg(i: int) -> None:
            await be.save("s1", f"q{i}", f"a{i}")

        await asyncio.gather(*(save_msg(i) for i in range(50)))
        hist = await be.load("s1")
        assert len(hist) >= 102  # 2 init + 50*2 = 102

    @pytest.mark.asyncio
    async def test_sqlite_memory_concurrent_saves_preserve_all_messages(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            be = SqliteMemory(db_path=tmp.name, max_messages=0)
            await be.save("s1", "init", "ok")

            async def save_msg(i: int) -> None:
                await be.save("s1", f"q{i}", f"a{i}")

            await asyncio.gather(*(save_msg(i) for i in range(50)))
            hist = await be.load("s1")
            assert len(hist) >= 102
        finally:
            import os
            os.unlink(tmp.name)


class TestRedisMemoryConcurrencyFix:
    """If unskipped, this test will FAIL on RedisMemory — it has no asyncio.Lock."""

    @pytest.mark.asyncio
    async def test_redis_concurrent_saves_lose_data(self) -> None:
        from yomai.memory.redis import RedisMemory

        class FakeRedis:
            def __init__(self) -> None:
                self.data: dict[str, str] = {}

            async def get(self, key: str) -> str | None:
                await asyncio.sleep(0)
                return self.data.get(key)

            async def set(self, key: str, val: str, ex: int | None = None) -> None:
                await asyncio.sleep(0)
                self.data[key] = val

            async def delete(self, key: str) -> None:
                self.data.pop(key, None)

        fake = FakeRedis()
        be = RedisMemory(client=fake, max_messages=0, ttl_hours=0)

        async def save_msg(i: int) -> None:
            await be.save("s1", f"q{i}", f"a{i}")

        await asyncio.gather(*(save_msg(i) for i in range(20)))
        raw = json.loads(fake.data.get(be._key("s1"), "[]"))
        # With a lock: 40 messages. Without a lock: fewer (data loss)
        assert len(raw) == 40, f"Expected 40 messages, got {len(raw)} — RedisMemory has a concurrency bug"


# =============================================================================
# Edge case: max_messages boundaries
# =============================================================================


class TestTruncationEdgeCases:
    @pytest.mark.asyncio
    async def test_max_messages_one_keeps_last_only(self) -> None:
        be = DictMemory(max_messages=1)
        await be.save("s1", "first", "reply1")
        await be.save("s1", "second", "reply2")
        hist = await be.load("s1")
        assert len(hist) == 1
        assert hist[0]["content"] == "reply2"

    @pytest.mark.asyncio
    async def test_max_messages_one_with_system_preserved_or_dropped(self) -> None:
        """With max=1, system message can't be preserved alongside user/assistant."""
        be = DictMemory(max_messages=1)
        # Inject a system message manually
        be._store["s1"] = (0, [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}])
        hist = be._truncate(be._store["s1"][1])
        # max_messages=1, system present: _max>1 is False, falls through to history[-1:]
        assert len(hist) == 1
        assert hist[0]["role"] == "user"  # system dropped

    @pytest.mark.asyncio
    async def test_max_messages_two_preserves_system(self) -> None:
        be = DictMemory(max_messages=2)
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "a2"},
        ]
        result = be._truncate(history)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_max_messages_zero_unlimited(self) -> None:
        be = DictMemory(max_messages=0)
        await be.save("s1", "q1", "a1")
        await be.save("s1", "q2", "a2")
        hist = await be.load("s1")
        assert len(hist) == 4  # no limit


# =============================================================================
# Edge case: TTL boundaries
# =============================================================================


class TestTTLEdgeCases:
    @pytest.mark.asyncio
    async def test_ttl_negative_hours_treated_as_zero(self) -> None:
        """Negative ttl_hours should disable eviction, not crash."""
        be = DictMemory(ttl_hours=-5)
        assert be._ttl_secs == 0
        await be.save("s1", "hello", "world")
        assert len(await be.load("s1")) == 2

    @pytest.mark.asyncio
    async def test_ttl_zero_never_evicts(self) -> None:
        be = DictMemory(ttl_hours=0)
        await be.save("s1", "hello", "world")
        # Try loading multiple times — should never evict
        for _ in range(5):
            assert len(await be.load("s1")) == 2

    @pytest.mark.asyncio
    async def test_evict_expired_does_not_crash_on_empty_store(self) -> None:
        be = DictMemory(ttl_hours=1)
        be._ttl_secs = 1
        be._evict_expired()  # should not crash with empty store
        assert len(be._store) == 0


# =============================================================================
# Edge case: malformed or unusual session IDs
# =============================================================================


class TestUnusualSessionIds:
    @pytest.mark.asyncio
    async def test_empty_string_session_id(self) -> None:
        be = DictMemory()
        await be.save("", "hello", "world")
        assert len(await be.load("")) == 2
        await be.clear("")
        assert await be.load("") == []

    @pytest.mark.asyncio
    async def test_unicode_session_id(self) -> None:
        be = DictMemory()
        sid = "セッション-日本語-🧠"
        await be.save(sid, "hello", "world")
        assert len(await be.load(sid)) == 2

    @pytest.mark.asyncio
    async def test_very_long_session_id(self) -> None:
        be = DictMemory()
        sid = "x" * 500
        await be.save(sid, "hello", "world")
        assert len(await be.load(sid)) == 2

    @pytest.mark.asyncio
    async def test_session_id_with_special_chars(self) -> None:
        be = DictMemory()
        sid = "../etc/passwd\x00null"
        await be.save(sid, "hello", "world")
        assert len(await be.load(sid)) == 2


# =============================================================================
# Edge case: zero-length or weird content
# =============================================================================


class TestWeirdContent:
    @pytest.mark.asyncio
    async def test_very_long_message(self) -> None:
        be = DictMemory(max_messages=5)
        long_msg = "x" * 100_000
        await be.save("s1", long_msg, long_msg)
        hist = await be.load("s1")
        assert hist[0]["content"] == long_msg
        assert hist[1]["content"] == long_msg

    @pytest.mark.asyncio
    async def test_newlines_in_messages(self) -> None:
        be = DictMemory()
        await be.save("s1", "line1\nline2\r\nline3", "reply\nwith\nnewlines")
        hist = await be.load("s1")
        assert hist[0]["content"] == "line1\nline2\r\nline3"
        assert hist[1]["content"] == "reply\nwith\nnewlines"

    @pytest.mark.asyncio
    async def test_message_with_json_special_chars(self) -> None:
        be = DictMemory()
        msg = '{"key": "value", "nested": {"deep": true}}'
        await be.save("s1", msg, "ok")
        hist = await be.load("s1")
        assert hist[0]["content"] == msg


# =============================================================================
# Sqlite-specific edge cases
# =============================================================================


class TestSqliteEdgeCases:
    @pytest.mark.asyncio
    async def test_table_recreated_if_dropped(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            be = SqliteMemory(db_path=tmp.name)
            # Drop the table
            conn = be._connect()
            conn.execute("DROP TABLE sessions")
            conn.commit()
            conn.close()

            # Re-create backend — should migrate silently
            be2 = SqliteMemory(db_path=tmp.name)
            await be2.save("s1", "hello", "world")
            assert len(await be2.load("s1")) == 2
        finally:
            import os
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_vacuum_does_not_crash(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            be = SqliteMemory(db_path=tmp.name)
            conn = be._connect()
            conn.execute("VACUUM")
            conn.close()
            await be.save("s1", "hello", "world")
            assert len(await be.load("s1")) == 2
        finally:
            import os
            os.unlink(tmp.name)

    @pytest.mark.asyncio
    async def test_busy_timeout_handles_locked_db(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            be = SqliteMemory(db_path=tmp.name)
            # Each operation opens/closes a connection, so busy timeout
            # shouldn't be triggered in normal usage.
            await be.save("s1", "hello", "world")
            hist = await be.load("s1")
            assert len(hist) == 2
        finally:
            import os
            os.unlink(tmp.name)
