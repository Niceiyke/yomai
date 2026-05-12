# Yomai v0.2.0 → v1.0.0 — Production Readiness Plan

## Overview

Yomai v0.1.0 is a solid foundation with an excellent API design (`@tool` / `@app.agent` / `@app.workflow`),
but needs work across reliability, security, observability, and operations before it can carry
production traffic. This plan breaks the work into 5 phases over ~6 weeks.

| Phase | Theme | Impact | Effort |
|-------|-------|--------|--------|
| 1 | Reliability — retries, timeouts, error recovery | Critical | ~1 week |
| 2 | Observability — structured logging, metrics, tracing | Critical | ~1 week |
| 3 | Security — auth, key management, input hardening | High | ~1 week |
| 4 | Operations — Docker, deployment, scaling, docs | High | ~1.5 weeks |
| 5 | Polish — provider coverage, budget enforcement, testing | Medium | ~1.5 weeks |

---

## Phase 1 — Reliability (~1 week)

### 1.1 LLM request retry with backoff

**Problem:** `AnthropicProvider.stream()` and `OpenAIProvider.stream()` raise `YomaiLLMError` on any
exception. There is no automatic retry for transient failures (rate limits, network timeouts, 5xx).

**Fix:**
- Add `max_retries` and `retry_backoff` to `LLMConfig` (default: 3 retries, exponential backoff 1s→2s→4s).
- Wrap provider `stream()` calls in `@retry` decorator or a reusable `retry_async` utility.
- Only retry on transient errors: `RateLimitError`, `APITimeoutError`, `5xx` status codes.
- Do NOT retry on `AuthenticationError` or `4xx` (except 429).

```python
# yomai/config.py
class LLMConfig(BaseModel):
    max_retries: int = 3
    retry_backoff_secs: float = 1.0
    retry_backoff_multiplier: float = 2.0
```

```python
# yomai/llm/_retry.py (new)
async def retry_with_backoff(fn, max_retries, backoff, multiplier, *transient_exceptions):
    ...
```

**Files:** `yomai/config.py`, `yomai/llm/anthropic.py`, `yomai/llm/openai.py`, new `yomai/llm/_retry.py`
**Tests:** Unit tests for retry logic, integration tests with mocked rate limit responses.

### 1.2 Tool execution timeout and retry

**Problem:** `AgentLoop._execute_tool_call()` runs tools with no per-tool timeout. A slow tool blocks
the entire agent loop. No retry on tool execution failures.

**Fix:**
- Add `timeout_secs` and `max_retries` to the `@tool` decorator.
- Wrap tool execution in `asyncio.wait_for(timeout=...)`.
- On `TimeoutError`, yield `sse_error("Tool timed out", "tool_timeout")` and return error result.
- On transient tool errors, retry up to `max_retries` times.

```python
@tool(timeout_secs=10, max_retries=1)
def lookup_order(order_id: str) -> str:
    ...
```

**Files:** `yomai/tools/decorator.py`, `yomai/core/agent.py`
**Tests:** Tool timeout test, tool retry test.

### 1.3 Workflow error recovery

**Problem:** If an async workflow fails at step N, all previous work is lost. No partial result
preservation, no resume from checkpoint.

**Fix:**
- Already have `StepCheckpoint` infrastructure — wire it into inline workflow execution.
- Save checkpoint after each successful step (not just in `WorkflowRunner.step()`).
- On retry, skip already-completed steps by checking checkpoints.
- Add `max_retries` to `@app.workflow(mode="async", max_retries=3)`.
- On final failure, store partial results and error context.

```python
@app.workflow("/triage", mode="async", max_retries=3)
async def triage(message: str, runner):
    ...
```

**Files:** `yomai/core/app.py` (`_run_inline_workflow_job`), `yomai/jobs/checkpoints.py`, `yomai/jobs/models.py`
**Tests:** Workflow retry from checkpoint, partial result preservation.

### 1.4 Graceful shutdown improvements

**Problem:** The 30-second drain timer may not be enough for long-running agent requests (up to 180s
by default). Active streams are force-cancelled after the drain window.

**Fix:**
- Add `shutdown_timeout_secs` to `StreamingConfig` (default: 30).
- During drain, stop accepting NEW connections immediately but let existing streams finish
  up to their configured `timeout_secs` / `max_duration_secs`.
- Add a hard deadline: `max(shutdown_timeout_secs, agent_config.timeout_secs)`.
- Emit `"server: shutdown"` SSE event to connected clients so they can reconnect gracefully.

**Files:** `yomai/core/app.py`, `yomai/config.py`
**Tests:** Shutdown drain with active connections.

### 1.5 SSE reconnection robustness

**Problem:** `Last-Event-ID` parsing was fixed for Redis stream IDs in Phase 2, but the client-side
reconnection story isn't tested end-to-end. Clients that reconnect mid-stream may miss events.

**Fix:**
- Emit `id:` on every SSE event consistently (already done for job streams, add for agent streams).
- Add a `retry:` SSE field to configure client reconnection delay.
- Test: disconnect client mid-stream, reconnect with `Last-Event-ID`, verify no duplicate/missed events.
- Add `X-Session-Id` header documentation for session continuity.

**Files:** `yomai/streaming/sse.py`, `yomai/core/router.py`
**Tests:** SSE reconnection with Last-Event-ID.

---

## Phase 2 — Observability (~1 week)

### 2.1 Structured logging

**Problem:** All logging uses `print()` statements (`StreamLog.emit()`, hook callbacks, CLI output).
No log levels, no structured fields, no log aggregation support.

**Fix:**
- Replace all `print()` calls with Python's `logging` module.
- Use structured log format: `{"timestamp": "...", "level": "INFO", "route": "/chat", "session_id": "...", "tokens_in": 100, "tokens_out": 50, "duration_ms": 1200, "tools": ["lookup_order"]}`.
- Support JSON log format via `YOMAI_LOG_FORMAT=json` env var.
- Log levels: `DEBUG` (SSE events), `INFO` (request start/end), `WARNING` (rate limits hit), `ERROR` (failures).
- Add `YOMAI_LOG_LEVEL` env var.

```python
# yomai/logging.py (new, replace StreamLog)
import structlog  # or just stdlib logging + json formatter

logger = structlog.get_logger("yomai")
logger.info("agent.request.complete", route="/chat", session_id="abc", ...)
```

**Files:** new `yomai/logging.py`, `yomai/middleware/logging.py`, `yomai/core/router.py`, `yomai/hooks.py`
**Tests:** Verify log output format and levels.

### 2.2 Prometheus metrics

**Problem:** The `/__yomai__/metrics` endpoint returns a custom JSON format. No Prometheus
compatibility, no histogram support, no labels/cardinality management.

**Fix:**
- Add optional `prometheus-client` dependency.
- Export metrics in Prometheus text format at `/__yomai__/metrics` (content-negotiated with JSON).
- Add histogram metrics: `yomai_request_duration_seconds` (by route, method), `yomai_tool_duration_seconds` (by tool name), `yomai_llm_tokens_total` (by provider, model).
- Add counter metrics: `yomai_requests_total`, `yomai_errors_total` (by error code), `yomai_rate_limits_total`.
- Add gauge metrics: `yomai_active_connections`, `yomai_jobs_queued`.

```python
# yomai/metrics.py (new)
from prometheus_client import Counter, Histogram, Gauge, generate_latest

request_duration = Histogram("yomai_request_duration_seconds", "...", ["method", "route", "status"])
```

**Files:** new `yomai/metrics.py`, `yomai/core/app.py`, `pyproject.toml` (optional dep)
**Tests:** Verify Prometheus output format.

### 2.3 OpenTelemetry tracing

**Problem:** No distributed tracing. Can't follow a request through agent loop → tool calls → LLM
provider in production monitoring tools.

**Fix:**
- Add optional `opentelemetry-api` + `opentelemetry-sdk` dependencies.
- Create spans: `yomai.request` (parent), `yomai.agent.loop` (child), `yomai.tool.execute` (child of loop), `yomai.llm.stream` (child of loop).
- Propagate trace context via `X-Trace-Id` / W3C Trace Context headers.
- Add span attributes: `session_id`, `route`, `tool_name`, `model`, `tokens_in`, `tokens_out`, `error`.

```python
# yomai/tracing.py (new)
from opentelemetry import trace

tracer = trace.get_tracer("yomai")

with tracer.start_as_current_span("yomai.tool.execute", attributes={"tool.name": name}) as span:
    result = await fn(**args)
    span.set_attribute("tool.result_length", len(str(result)))
```

**Files:** new `yomai/tracing.py`, `yomai/core/agent.py`, `yomai/core/router.py`, `pyproject.toml`
**Tests:** Verify span structure with OTLP test collector.

### 2.4 Health check depth

**Problem:** `GET /__yomai__/health` returns `{"status": "ok"}` with no dependency checks.

**Fix:**
- Add `?depth=deep` query param to check downstream dependencies.
- Shallow (`depth=shallow`, default): returns 200 if process is alive.
- Deep (`depth=deep`): checks LLM provider connectivity (quick model list call), Redis ping
  (if configured), SQLite writeability test.
- Return per-dependency status.

```json
{
  "status": "ok",
  "version": "0.2.0",
  "dependencies": {
    "llm": {"status": "ok", "provider": "anthropic", "latency_ms": 45},
    "redis": {"status": "ok", "latency_ms": 2},
    "sqlite": {"status": "ok"}
  }
}
```

**Files:** `yomai/core/app.py` (`_health`), `yomai/config.py`
**Tests:** Deep health check with mocked dependencies.

---

## Phase 3 — Security (~1 week)

### 3.1 Authentication system

**Problem:** Only API key auth via `Authorization: Bearer <key>` on metadata endpoints.
No per-route auth, no JWT/OAuth2, no API key rotation, no scope-based access.

**Fix:**
- Add `AuthBackend` protocol (like `MemoryBackend`).
- Built-in backends: `APIKeyAuth`, `JWTAuth`, `NoAuth`.
- Configure at app level OR per-route:

```python
app = Yomai(auth=APIKeyAuth(keys=["sk-xxx"], header="X-API-Key"))
# or per-route:
@app.agent("/chat", auth=JWTAuth(secret="...", audience="yomai"))
```

- Auth result injected into handler via `Depends` or `request.state.user`.
- Rate limit scoped per API key, not just per session.

**Files:** new `yomai/auth/` package, `yomai/core/app.py`, `yomai/core/_base_route.py`
**Tests:** JWT generation/validation, API key rotation, auth error responses.

### 3.2 Input validation hardening

**Problem:** We added 10MB body size limit, but no max field length, no JSON depth limit,
no content-type enforcement, no CSRF protection.

**Fix:**
- Add `max_field_length` to `StreamingConfig` (default: 10,000 chars for `message` field).
- Add `max_json_depth` (default: 10 levels).
- Validate `Content-Type: application/json` on POST/PUT/PATCH.
- Add CSRF token requirement for browser-facing POST endpoints (optional, via middleware).
- Sanitize user input before passing to LLM — strip control characters, enforce max length.

```python
class StreamingConfig(BaseModel):
    max_body_bytes: int = 10_485_760  # 10 MB
    max_field_length: int = 10_000
    max_json_depth: int = 10
```

**Files:** `yomai/_types.py` (`read_json_body`), `yomai/config.py`, new `yomai/middleware/input_validation.py`
**Tests:** Oversized fields, deep JSON nesting, missing content-type.

### 3.3 Secret management

**Problem:** API keys read from env vars (good) but also accepted in `LLMConfig(api_key="...")`
which encourages hardcoding. No support for secret stores (Vault, AWS Secrets Manager).

**Fix:**
- Add `SecretProvider` protocol.
- Built-in: `EnvSecret` (env vars, current behavior), `FileSecret` (read from file path).
- Support for external secret stores via pluggable provider.
- Log warning if `api_key` is passed directly to config (not from env).
- Add `.env.example` warning in scaffolded projects.

```python
LLMConfig(api_key=FileSecret("/run/secrets/anthropic_key"))
```

**Files:** new `yomai/secrets.py`, `yomai/config.py`
**Tests:** File secret loading, env fallback.

### 3.4 Rate limiting per key/scope

**Problem:** Rate limiting is per-session only. No per-API-key limits, no global rate limiting,
no cost-based rate limiting.

**Fix:**
- Add `RateLimitScope`: `"session"`, `"api_key"`, `"global"`.
- `RateLimitConfig` supports per-scope limits.
- `RedisRateLimiter` supports all scopes.
- Add `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers to responses.

```python
app = Yomai(
    rate_limits=RateLimitConfig(
        per_session_rpm=20,
        per_api_key_rpm=100,
        global_rpm=1000,
    )
)
```

**Files:** `yomai/limits.py`, `yomai/config.py`, `yomai/core/app.py`
**Tests:** Per-scope rate limiting, header verification.

---

## Phase 4 — Operations (~1.5 weeks)

### 4.1 Docker & docker-compose

**Problem:** No containerization. Users must manually install Python, uv, Redis.

**Fix:**
- `Dockerfile`: multi-stage build, `python:3.12-slim` base, `uv` for deps, runs `uvicorn`.
- `docker-compose.yml`: `yomai` + `redis` + optional `postgres`/`prometheus`.
- `.dockerignore`.
- `scripts/docker-entrypoint.sh` for migrations and health checks.
- Publish to GitHub Container Registry via CI.

```dockerfile
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
...
```

**Files:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `scripts/docker-entrypoint.sh`
**Tests:** Container smoke test in CI.

### 4.2 Production server config

**Problem:** `uvicorn.run(app, ...)` in the CLI uses development defaults. No production uvicorn
config with multiple workers, proper logging, SSL.

**Fix:**
- Add `Yomai.serve()` method that configures uvicorn for production.
- Read workers from `YOMAI_WORKERS` env var (default: cpu_count).
- Support `--ssl-keyfile` / `--ssl-certfile` for HTTPS.
- Add `--proxy-headers` for behind-nginx deployments.
- Graceful worker shutdown with `--timeout-graceful-shutdown`.
- Production-ready gunicorn config for process management.

```bash
yomai serve main:app --workers 4 --proxy-headers
```

**Files:** `yomai/cli/main.py`, new `yomai/server.py`
**Tests:** Server startup with various configs.

### 4.3 Database migrations for SQLite

**Problem:** `SqliteMemory._init_db()` auto-creates/alters tables. No versioned migration system.

**Fix:**
- Add `schema_version` table.
- On startup, check version and apply migrations sequentially.
- Support for custom backends to register their own migrations.

**Files:** `yomai/memory/sqlite.py`, new `yomai/memory/_migrations.py`
**Tests:** Migration from v0→v1, error on downgrade.

### 4.4 Deployment guides

**Problem:** No documentation on how to deploy Yomai in production.

**Fix:**
- `docs/deployment.md`: Covers Docker, docker-compose, Kubernetes, fly.io, Railway.
- Nginx reverse proxy config with SSE support (`proxy_buffering off`).
- Systemd service file.
- Health check endpoint configuration.
- TLS termination options.
- Environment variable reference (already part of `yomai/env.py`).

**Files:** `docs/deployment.md`
**Tests:** N/A (docs only).

### 4.5 CI/CD improvements

**Problem:** CI builds but doesn't publish. No release automation, no changelog generation.

**Fix:**
- Add GitHub Actions workflow for release: build → test → publish to PyPI + GHCR.
- Add `release-please` or `semantic-release` for automated versioning.
- Generate `CHANGELOG.md` from conventional commits.
- Publish Docker image to `ghcr.io/yomai/yomai:latest` on tag.
- Add `uv build` artifact upload for PyPI.

**Files:** `.github/workflows/release.yml`, `.github/workflows/ci.yml`
**Tests:** Dry-run release in CI.

---

## Phase 5 — Polish (~1.5 weeks)

### 5.1 LLM provider coverage

**Problem:** Only Anthropic and OpenAI. Missing Google Gemini, Mistral, local models, Azure OpenAI.

**Fix:**
- `GoogleProvider` — wraps `google-generativeai` SDK. Maps Gemini streaming to `LLMEvent`.
- `MistralProvider` — wraps `mistralai` SDK.
- `OllamaProvider` — uses OpenAI-compatible `/v1/chat/completions` endpoint (Ollama supports this).
- `AzureOpenAIProvider` — extends `OpenAIProvider` with `azure_endpoint` + `api_version`.
- Provider auto-detection from environment.

```python
# All providers follow the same pattern:
class GoogleProvider(LLMProvider):
    def stream(self, messages, tools, system) -> AsyncIterator[LLMEvent]: ...
    def tool_schemas(self, tools) -> list[ToolSchema]: ...
    def tool_result_messages(self, tool_call, result) -> list[Message]: ...
```

**Files:** new `yomai/llm/google.py`, `yomai/llm/mistral.py`, `yomai/llm/ollama.py`, `yomai/llm/azure.py`
**Tests:** Unit tests for each provider's event streaming, tool schema conversion.

### 5.2 Budget enforcement

**Problem:** `BudgetConfig` is defined but `on_exceeded` logic is never called. Token counting
happens per-request but no cumulative tracking.

**Fix:**
- Track cumulative token usage per session in memory backend.
- Add `budget.check()` call after each LLM response.
- On exceed: `"stop"` mode returns error and stops further LLM calls. `"warn"` mode logs warning
  but continues.
- Track daily cost via `RedisBudgetStore` or in-memory with periodic flush.
- Add `X-Yomai-Budget-Remaining` response header.

```python
# In AgentLoop.run():
async for event in self.provider.stream(...):
    if isinstance(event, Done):
        exceeded = await budget.check(session_id, event.input_tokens, event.output_tokens)
        if exceeded and budget.on_exceeded == "stop":
            yield sse_error("Budget exceeded", "budget_exceeded")
            return
```

**Files:** new `yomai/budget.py`, `yomai/core/agent.py`, `yomai/core/app.py`, `yomai/config.py`
**Tests:** Token budget exhaustion, cost budget exhaustion, warn vs stop.

### 5.3 LLM provider test coverage

**Problem:** 15-25% line coverage on LLM providers. Streaming logic is complex and poorly tested.

**Fix:**
- Use `aresponses` or `pytest-httpx` to mock HTTP responses from Anthropic/OpenAI APIs.
- Test: streaming text chunks, streaming tool calls, interleaved text+tool, error responses,
  rate limit responses, empty streams, truncated JSON in tool args.
- Test: `AnthropicProvider.tool_schemas()` with real registered tools.
- Test: `AnthropicProvider.tool_result_messages()` message format correctness.
- Goal: 85%+ coverage on LLM provider files.

**Files:** `tests/test_llm_anthropic.py`, `tests/test_llm_openai.py` (new)
**Tests:** ~30 new test cases.

### 5.4 Documentation site

**Problem:** API reference exists (`docs/api-reference.md`) but no tutorial, no getting-started,
no migration guide, no architecture doc.

**Fix:**
- `docs/getting-started.md`: 5-minute quickstart from `pip install` to first agent.
- `docs/tutorial.md`: Build a weather agent step-by-step with explanations.
- `docs/architecture.md`: System overview, request lifecycle, SSE protocol, memory model.
- `docs/migration.md`: Breaking changes between versions.
- `docs/examples/`: Showcase the support agent, weather agent, research workflow.
- Generate docs site with MkDocs or VitePress, deploy to GitHub Pages via CI.
- Add `docs/` to `.pre-commit-config.yaml` for link checking.

**Files:** `docs/*.md`, `mkdocs.yml` or `docs/.vitepress/`, `.github/workflows/docs.yml`
**Tests:** Link checker in CI.

### 5.5 Performance benchmarking suite

**Problem:** One-off benchmark script exists (`scripts/bench_phases.py`) but no automated
benchmarking against regressions.

**Fix:**
- Add `pytest-benchmark` to dev dependencies.
- Benchmark: SSE format sanitization, strip_reasoning, tool argument validation,
  AgentLoop.run() with mock LLM (tokens/sec throughput).
- Run benchmarks in CI on push to main, store results.
- Alert on >10% regression.

**Files:** `tests/test_benchmarks.py` (new), `pyproject.toml`, `.github/workflows/bench.yml`
**Tests:** Benchmark assertions that stay within performance budget.

---

## Summary — End State

| Dimension | v0.1.0 (Current) | v1.0.0 (Target) |
|-----------|-----------------|-----------------|
| Auth | API key on metadata | JWT + API key + OAuth2, per-route |
| LLM providers | Anthropic, OpenAI | + Gemini, Mistral, Ollama, Azure |
| Retries | None | LLM + tool + workflow, exponential backoff |
| Observability | `print()` + JSON metrics | Structured logging + Prometheus + OTEL |
| Deployment | Manual `uvicorn` | Docker + compose + K8s + fly.io |
| Testing coverage | 80% overall, 15% LLM | 85% overall, 85% LLM providers |
| Budget enforcement | Config exists, not wired | Per-session/day cost tracking |
| Documentation | API ref only | Tutorials + deployment + architecture |
| Health check | Process alive | Deep checks: LLM, Redis, SQLite |
| Rate limiting | Per-session | Per-key + global + cost-based |

## Timeline & milestones

```
Week 1-2:  Phase 1 (Reliability) + Phase 2 (Observability) → v0.2.0
Week 3:    Phase 3 (Security) → v0.3.0
Week 4-5:  Phase 4 (Operations) → v0.4.0
Week 5-6:  Phase 5 (Polish) → v1.0.0-rc1
Week 6:    Bug fixes from RC → v1.0.0
```
