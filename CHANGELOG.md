# Changelog

## 0.2.0

### Production readiness — 5-phase overhaul

**Phase 1 — Reliability**
- LLM request retry with exponential backoff (Anthropic + OpenAI)
- Tool execution timeout via `@tool(timeout_secs=N)` and retry via `max_retries`
- Async workflow retry via `@app.workflow(max_retries=N)`
- Configurable shutdown timeout (`StreamingConfig.shutdown_timeout_secs`)
- SSE reconnection: `id:` field on all agent stream events for `Last-Event-ID` replay

**Phase 2 — Observability**
- Structured JSON logging (`YOMAI_LOG_FORMAT`/`YOMAI_LOG_LEVEL` env vars)
- Prometheus metrics endpoint (`yomai[metrics]` extra) with counters, histograms, gauges
- Deep health checks: `GET /__yomai__/health?depth=deep` tests LLM+Redis+SQLite

**Phase 3 — Security**
- Auth system: `AuthBackend` protocol, `APIKeyAuth`, `JWTAuth`, `NoAuth`
- `Yomai(auth=...)` app-level + per-route `auth=` override
- `request.state.yomai_auth` stores identity, scopes, metadata
- Input validation: max field length (100KB), max JSON depth (20), body size limit (10MB)

**Phase 4 — Operations**
- Multi-stage Dockerfile (python:3.12-slim + uv) with HEALTHCHECK
- `docker-compose.yml`: Yomai + Redis with health checks
- `yomai serve` command: multi-worker, proxy-headers, env-configurable
- Release CI: push tag → publish to PyPI + GHCR
- Deployment guide with nginx reverse proxy config

**Phase 5 — Polish**
- Ollama provider support (`LLMConfig(provider="ollama")`)
- Budget enforcement: `BudgetConfig` now wired — tracks tokens/cost per session + daily,
  stops or warns on exceeded limits
- SSE event sanitization (newline injection protection)
- `strip_reasoning` performance: 150x faster via `str.find` instead of char iteration
- `@tool(timeout_secs=, max_retries=)` decorator options

**Bug fixes (17 total)**
- SSE newline injection sanitization
- Data races on `_active_connections` and `_metrics_counters` (added `asyncio.Lock`)
- Dead code removed from `_validate_new_path`
- `Last-Event-ID` parsing for Redis stream IDs
- Consumer task leak in `_run_inline_workflow_job`
- `_validate_tool_args` now handles generics (`list[T]`, `Optional[T]`, `dict[K,V]`)
- 10 MB request body size limit
- BaseRoute extraction eliminated 200+ lines of duplicated CORS/auth/dep code
- `request: Request` parameter injected into handler kwargs
- Centralized `yomai/env.py` for all environment variables
- Added ruff linting + `.pre-commit-config.yaml`
- `session_id` properly injected in async workflow kwargs

**Support Agent demo app**
- Full customer support application: streaming chat, async triage workflow, REST analytics
- React + Vite + TypeScript + Tailwind dashboard frontend
- 6 integration tests + 13 new core tests (113 total)

## 0.1.0

Initial pre-release baseline.

Features:
- Starlette-based `Yomai` ASGI app
- `@app.agent` routes with SSE streaming
- Agent handlers run before the LLM loop and can receive extra JSON body fields
- `@tool` decorator and sequential tool execution loop
- Route-scoped tool execution with signature binding and basic runtime argument checks
- Basic `Literal[...]` enum support in tool schemas
- Anthropic and OpenAI-compatible providers with provider-specific defaults
- Dict and SQLite session memory with max-message truncation and TTL eviction
- SQLite memory uses WAL mode and a busy timeout
- `@app.workflow` and `WorkflowRunner`
- Testing utilities: `YomaiTestClient`, `mock_llm`, `capture_tools`
- Dev playground at `/__yomai__`
- Production metadata endpoint auth when `YOMAI_API_KEY` is configured
- Per-route API key overrides for agents and workflows
- Pydantic validation/coercion for agent extra body fields and workflow inputs
- OpenAPI components for registered tool schemas
- Optional signed session middleware
- Production-safe streaming error messages
- CLI: `yomai new`, `yomai run`
- Typed package with `py.typed`
