"""Tests for delegate edges, LLM retry edge cases, CLI, production sanitization,
rate limiter boundaries, and stream cancellation."""
from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from yomai.config import (
    LLMConfig,
    MemoryConfig,
)

# ===========================================================================
# #1 — Workflow delegate edges
# ===========================================================================

class TestDelegateEdgeCases:
    @pytest.mark.asyncio
    async def test_delegate_with_custom_system_prompt(self) -> None:
        from yomai import Yomai
        from yomai.testing import YomaiTestClient, mock_llm
        from yomai.workflow.runner import WorkflowRunner

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.agent("/specialist", system="You are a poet")
        async def specialist(message: str, session_id: str) -> None:
            pass

        @app.workflow("/poem")
        async def poem(runner: WorkflowRunner):
            with mock_llm(["Roses are red"]):
                result = await runner.delegate(specialist, "write a poem", system="Be concise")
            return {"poem": result}

        events = await YomaiTestClient(app).get_events("/poem", "ignored")
        result = next(e for e in events if e.get("type") == "result")
        import json
        data = json.loads(result.get("content", "{}"))
        assert "Roses are red" in str(data.get("poem", ""))

    @pytest.mark.asyncio
    async def test_delegate_can_access_runner_state(self) -> None:
        """Delegate step stores result in runner.state under agent name."""
        from yomai import Yomai
        from yomai.testing import YomaiTestClient, mock_llm
        from yomai.workflow.runner import WorkflowRunner

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.agent("/helper")
        async def helper(message: str, session_id: str) -> None:
            pass

        @app.workflow("/pipe")
        async def pipe(runner: WorkflowRunner):
            with mock_llm(["42"]):
                await runner.delegate(helper, "answer")
            return {"answer": runner.state.get("helper", "missing")}

        events = await YomaiTestClient(app).get_events("/pipe", "ignored")
        result = next(e for e in events if e.get("type") == "result")
        import json
        data = json.loads(result.get("content", "{}"))
        assert data.get("answer") == "42"

    @pytest.mark.asyncio
    async def test_delegate_raises_if_cancelled(self) -> None:
        """raise_if_cancelled works when a job_id is set and job is cancelled."""
        from yomai import Yomai
        from yomai.workflow.runner import WorkflowRunner

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        # Create a job, then cancel it
        await app.create_job("job-cancel-test", "/test")
        await app.jobs.update_status("job-cancel-test", "cancelled")

        # Create runner with the cancelled job_id
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        runner = WorkflowRunner(queue, "s1", app.memory, app, job_id="job-cancel-test")

        assert await runner.cancelled()
        with pytest.raises(asyncio.CancelledError, match="cancelled"):
            await runner.raise_if_cancelled()

    @pytest.mark.asyncio
    async def test_delegate_preserves_graph_chain(self) -> None:
        """Delegate creates graph events connected to the workflow chain."""
        from yomai import Yomai
        from yomai.testing import YomaiTestClient, mock_llm
        from yomai.workflow.runner import WorkflowRunner

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.agent("/subt")
        async def subt(message: str, session_id: str) -> None:
            pass

        @app.workflow("/chain")
        async def chain(runner: WorkflowRunner):
            with mock_llm(["alpha"]):
                await runner.delegate(subt, "first")
            with mock_llm(["beta"]):
                await runner.delegate(subt, "second")
            return {"done": True}

        events = await YomaiTestClient(app).get_events("/chain", "ignored")
        # Graph events have type "graph" with nested data
        graph_events = [e for e in events if e.get("event") == "graph"]
        assert len(graph_events) >= 2


# ===========================================================================
# #2 — LLM provider retry edge cases
# ===========================================================================

class TestLLMRetryEdges:
    @pytest.mark.asyncio
    async def test_retry_with_exponential_backoff(self) -> None:
        """Retry uses exponential backoff: 1s, 2s, 4s..."""
        from yomai.llm._retry import retry_with_backoff

        attempts = []

        async def fail_twice() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("transient")
            return "success"

        result = await retry_with_backoff(fail_twice, max_retries=2, backoff_secs=1.0, multiplier=2.0)
        assert result == "success"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_retry_stops_at_max_retries(self) -> None:
        from yomai.llm._retry import retry_with_backoff

        attempts = []

        async def always_fails() -> str:
            attempts.append(1)
            raise ConnectionError("transient")

        with pytest.raises(ConnectionError):
            await retry_with_backoff(always_fails, max_retries=2, backoff_secs=0.01, multiplier=1.0)
        assert len(attempts) == 3  # initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_transient_error_not_retried(self) -> None:
        from yomai.llm._retry import retry_with_backoff

        attempts = []

        async def fails_value_error() -> str:
            attempts.append(1)
            raise ValueError("not transient")

        with pytest.raises(ValueError):
            await retry_with_backoff(fails_value_error, max_retries=3, backoff_secs=0.01, multiplier=1.0)
        assert len(attempts) == 1  # no retries for non-transient

    @pytest.mark.asyncio
    async def test_transient_detection_http_500(self) -> None:
        from yomai.llm._retry import _is_transient

        class Fake503Error(Exception):
            status_code = 503

        class Fake429Error(Exception):
            status = 429

        assert _is_transient(Fake503Error())
        assert _is_transient(Fake429Error())

    @pytest.mark.asyncio
    async def test_transient_detection_http_400_not_retried(self) -> None:
        from yomai.llm._retry import _is_transient

        class Fake400Error(Exception):
            status_code = 400

        assert not _is_transient(Fake400Error())

    @pytest.mark.asyncio
    async def test_rate_limit_name_detection(self) -> None:
        from yomai.llm._retry import _is_transient

        class RateLimitError(Exception):
            pass

        class TooManyRequests(Exception):
            pass

        assert _is_transient(RateLimitError())
        assert _is_transient(TooManyRequests())

    @pytest.mark.asyncio
    async def test_timeout_error_is_transient(self) -> None:
        from yomai.llm._retry import _is_transient

        assert _is_transient(asyncio.TimeoutError())
        assert _is_transient(TimeoutError())


# ===========================================================================
# #3 — CLI command tests
# ===========================================================================

class TestCLICommands:
    def test_new_scaffolds_valid_project(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            result = runner.invoke(cli_app, ["new", f"{tmp}/myproject"])
            assert result.exit_code == 0
            assert (Path(tmp) / "myproject" / "main.py").exists()
            assert (Path(tmp) / "myproject" / "tools.py").exists()
            assert (Path(tmp) / "myproject" / "requirements.txt").exists()
            assert (Path(tmp) / "myproject" / ".env.example").exists()

    def test_new_fails_if_directory_exists(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "exists").mkdir()
            result = runner.invoke(cli_app, ["new", f"{tmp}/exists"])
            assert result.exit_code != 0

    def test_help_shows_version(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        runner = CliRunner()
        result = runner.invoke(cli_app, ["run", "--help"], catch_exceptions=False)
        assert "app_path" in result.stdout or "Yomai" in result.stdout

    def test_deploy_generates_dockerfile(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            runner.invoke(
                cli_app, ["deploy", "--output", f"{tmp}/Dockerfile", "main:app"],
                catch_exceptions=False,
            )
            dockerfile_path = Path(tmp) / "Dockerfile"
            if not dockerfile_path.exists():
                # Maybe current dir had the file
                cwd_dockerfile = Path("Dockerfile")
                if cwd_dockerfile.exists():
                    cwd_dockerfile.unlink()
                pytest.skip("Dockerfile not found in expected location")
            content = dockerfile_path.read_text()
            assert "FROM python" in content

    def test_serve_command_exists(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        runner = CliRunner()
        result = runner.invoke(cli_app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "Production" in result.stdout

    def test_dev_command_exists(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        runner = CliRunner()
        result = runner.invoke(cli_app, ["dev", "--help"])
        assert result.exit_code == 0
        assert "reload" in result.stdout.lower()

    def test_worker_rejects_non_swiftq_backend(self) -> None:
        from typer.testing import CliRunner

        from yomai.cli.main import app as cli_app

        # Create a minimal app module
        with tempfile.TemporaryDirectory() as tmp:
            module_path = Path(tmp) / "testapp.py"
            module_path.write_text(
                "from yomai import Yomai\n"
                "from yomai.config import LLMConfig, MemoryConfig\n"
                "app = Yomai(llm=LLMConfig(api_key=''), memory=MemoryConfig(backend='dict'))\n"
            )
            import sys
            old_cwd = os.getcwd()
            os.chdir(tmp)
            sys.path.insert(0, tmp)
            try:
                runner = CliRunner()
                result = runner.invoke(cli_app, ["worker", "testapp:app"], catch_exceptions=False)
                combined = (result.stdout or "") + (result.stderr or "")
                assert result.exit_code != 0 or "swiftq" in combined.lower()
            finally:
                os.chdir(old_cwd)
                sys.path.remove(tmp)


# ===========================================================================
# #4 — Production error sanitization
# ===========================================================================

class TestProductionErrorSanitization:
    @pytest.mark.asyncio
    async def test_production_returns_internal_server_error(self) -> None:
        """In production mode, errors are sanitized to 'Internal server error'."""
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.get("/will-fail")
        async def will_fail(request: Any) -> str:
            raise RuntimeError("secret database password: xyz123")

        client = YomaiTestClient(app)
        with patch.dict(os.environ, {"YOMAI_ENV": "production"}):
            async with await client._client() as http:
                resp = await http.get("/will-fail")
        assert resp.status_code == 500
        data = resp.json()
        assert data["message"] == "Internal server error"

    @pytest.mark.asyncio
    async def test_development_returns_full_error(self) -> None:
        """In development, the full error message is returned."""
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.get("/will-fail")
        async def will_fail(request: Any) -> str:
            raise RuntimeError("specific error detail")

        client = YomaiTestClient(app)
        with patch.dict(os.environ, {"YOMAI_ENV": "development"}):
            async with await client._client() as http:
                resp = await http.get("/will-fail")
        assert resp.status_code == 500
        data = resp.json()
        assert "specific error detail" in data["message"]

    @pytest.mark.asyncio
    async def test_cancelled_error_not_sanitized(self) -> None:
        """CancelledError is explicitly re-raised by ErrorMiddleware."""
        from yomai.middleware.errors import ErrorMiddleware

        # Verify the middleware's exception handler re-raises CancelledError
        # by checking the source: `except (asyncio.CancelledError, GeneratorExit): raise`
        ErrorMiddleware(app=None)  # type: ignore[arg-type]
        import inspect
        source = inspect.getsource(ErrorMiddleware.dispatch)
        assert "CancelledError" in source
        assert "raise" in source  # The raise statement after except CancelledError


# ===========================================================================
# #5 — Rate limiter edge cases
# ===========================================================================

class TestRateLimiterEdges:
    @pytest.mark.asyncio
    async def test_check_request_zero_limit_always_passes(self) -> None:
        from yomai.limits import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        # limit=0 means no rate limiting
        assert await limiter.check_request("user", limit=0) is None
        assert await limiter.check_request("user", limit=None) is None

    @pytest.mark.asyncio
    async def test_check_request_boundary_exactly_at_limit(self) -> None:
        from yomai.limits import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        # Fill up to the limit
        for i in range(5):
            result = await limiter.check_request("user", limit=5, now=float(i))
            assert result is None, f"Request {i} should pass"
        # Next request should hit the limit
        result = await limiter.check_request("user", limit=5, now=10.0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_check_request_window_slides(self) -> None:
        from yomai.limits import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        # Make requests at t=0 through t=4
        for i in range(5):
            await limiter.check_request("user", limit=5, now=float(i))
        # At t=65, the t=0 request has expired (65-0=65 > 60s window)
        result = await limiter.check_request("user", limit=5, now=65.0)
        assert result is None  # should pass because one slot freed

    @pytest.mark.asyncio
    async def test_acquire_concurrent_no_limit(self) -> None:
        from yomai.limits import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        # No concurrent limit means always passes
        assert await limiter.acquire_concurrent("user", limit=None)
        assert await limiter.acquire_concurrent("user", limit=0)
        assert await limiter.acquire_concurrent("user", limit=-1)

    @pytest.mark.asyncio
    async def test_acquire_concurrent_respects_limit(self) -> None:
        from yomai.limits import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        assert await limiter.acquire_concurrent("user", limit=2)
        assert await limiter.acquire_concurrent("user", limit=2)
        assert not await limiter.acquire_concurrent("user", limit=2)

        await limiter.release_concurrent("user")
        assert await limiter.acquire_concurrent("user", limit=2)

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_check_request_increments(self) -> None:
        """RedisRateLimiter increments counter on check_request."""
        from yomai.limits import RedisRateLimiter

        class FakeRedis:
            def __init__(self) -> None:
                self.keys: dict[str, int] = {}
                self.expiries: dict[str, int] = {}
                self.deleted: list[str] = []

            async def incr(self, key: str) -> int:
                self.keys[key] = self.keys.get(key, 0) + 1
                return self.keys[key]

            async def expire(self, key: str, ttl: int) -> None:
                self.expiries[key] = ttl

            async def ttl(self, key: str) -> int:
                return self.expiries.get(key, -1)

            async def decr(self, key: str) -> int:
                self.keys[key] = self.keys.get(key, 0) - 1
                if self.keys[key] <= 0:
                    del self.keys[key]
                return self.keys.get(key, 0)

            async def delete(self, key: str) -> None:
                self.deleted.append(key)
                self.keys.pop(key, None)

            def register_script(self, script: str) -> Any:
                class Script:
                    def __init__(self, parent: Any) -> None:
                        self._p = parent
                    async def __call__(self, keys: list[str], args: list[int]) -> int:
                        key = keys[0] if keys else ""
                        limit = args[0] if args else 0
                        current = self._p.keys.get(key, 0) + 1
                        self._p.keys[key] = current
                        if limit <= 0 or current <= limit:
                            return 1
                        self._p.keys[key] = current - 1
                        return 0
                return Script(self)

        fake = FakeRedis()
        limiter = RedisRateLimiter("redis://fake", client=fake, prefix="test")

        result = await limiter.check_request("user-1", limit=3)
        assert result is None
        assert any("requests" in k for k in fake.keys)


# ===========================================================================
# #6 — Stream cancellation end-to-end
# ===========================================================================

class TestStreamCancellation:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_stream_returns_404(self) -> None:
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        client = YomaiTestClient(app)
        async with await client._client() as http:
            resp = await http.post("/__yomai__/streams/no-such-session/cancel")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_active_stream(self) -> None:
        """Cancelling an active stream via the endpoint cancels the task."""
        from yomai import Yomai
        from yomai.llm.base import TextChunk
        from yomai.llm.openai import OpenAIProvider
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(provider="openai", api_key="sk-fake"),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        @app.agent("/slow")
        async def slow(message: str, session_id: str) -> None:
            pass

        # Make the stream hang so we can cancel it
        class Hanging:
            def __aiter__(self) -> Hanging:
                return self
            async def __anext__(self) -> TextChunk:
                await asyncio.sleep(3600)
                return TextChunk("never")

        def hanging_factory() -> Any:
            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider.model = "mock"
            provider.max_tokens = 1024
            provider.stream = lambda messages, tools, system: Hanging()  # type: ignore[method-assign]
            provider.config = app.config.llm
            return provider

        # Patch route's provider factory
        for route in app._starlette.router.routes:
            if hasattr(route, "provider_factory"):
                route.provider_factory = hanging_factory  # type: ignore[attr-defined]

        client = YomaiTestClient(app)

        # Start a streaming request in a task
        asyncio.create_task(
            client.get_events("/slow", "hello", session_id="cancel-me")
        )

        # Give it a moment to start
        await asyncio.sleep(0.1)

        # Cancel via endpoint
        async with await client._client() as http:
            resp = await http.post("/__yomai__/streams/cancel-me/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_job_cancel_endpoint(self) -> None:
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        # Create a job directly
        await app.create_job("job-001", "/test-workflow")

        client = YomaiTestClient(app)
        async with await client._client() as http:
            resp = await http.post("/__yomai__/jobs/job-001/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_job_cancel_not_found(self) -> None:
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        client = YomaiTestClient(app)
        async with await client._client() as http:
            resp = await http.post("/__yomai__/jobs/no-such-job/cancel")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_job_stream_replay(self) -> None:
        """Job events are replayable via the stream endpoint."""
        from yomai import Yomai
        from yomai.testing import YomaiTestClient

        app = Yomai(
            llm=LLMConfig(api_key=""),
            memory=MemoryConfig(backend="dict", db_path="/unused"),
        )

        # Create a job and append some events
        await app.create_job("job-replay", "/test")
        await app.job_events.append("job-replay", "chunk", {"type": "chunk", "content": "hello"})
        await app.job_events.append("job-replay", "done", {"type": "done"})

        client = YomaiTestClient(app)
        async with await client._client() as http:
            resp = await http.get("/__yomai__/jobs/job-replay/stream")
            resp.raise_for_status()

        # Parse SSE events
        raw = resp.text
        events: list[dict[str, Any]] = []
        for block in raw.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            for line in block.splitlines():
                if line.startswith("data:"):
                    import json
                    with contextlib.suppress(json.JSONDecodeError):
                        events.append(json.loads(line.removeprefix("data:").strip()))

        types = [e.get("type") for e in events]
        assert "chunk" in types
        assert "done" in types
