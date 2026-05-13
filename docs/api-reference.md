# Yomai API Reference

> **Version:** 0.1.0

Complete reference for the public API surface of the Yomai framework. All classes, functions, and configuration models documented here are importable from the top-level `yomai` package unless otherwise noted.

---

## Table of Contents

1. [Yomai Application Class](#1-yomai-application-class)
2. [Configuration Models](#2-configuration-models)
3. [Decorators](#3-decorators)
4. [Hooks](#4-hooks)
5. [SSE Utilities](#5-sse-utilities)
6. [Workflow](#6-workflow)
7. [Memory Backends](#7-memory-backends)
8. [Testing](#8-testing)
9. [Environment Variables](#9-environment-variables)
10. [Jobs](#10-jobs)
11. [Plugins](#11-plugins)

---

## 1. Yomai Application Class

### `Yomai`

The main application class. Wraps a Starlette ASGI app and provides decorator-based routing for agents, workflows, and REST endpoints.

**Import:** `from yomai import Yomai`

```python
class Yomai:
    def __init__(
        self,
        llm: LLMConfig | None = None,
        memory: MemoryConfig | None = None,
        agent: AgentConfig | None = None,
        streaming: StreamingConfig | None = None,
        queue: QueueConfig | None = None,
        rate_limits: RateLimitConfig | None = None,
        budgets: BudgetConfig | None = None,
        dev: DevConfig | None = None,
        plugins: list[PluginSetup | str] | None = None,
    ) -> None:
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `LLMConfig` | `LLMConfig()` | LLM provider, model, and API key configuration |
| `memory` | `MemoryConfig` | `MemoryConfig()` | Session memory backend and TTL settings |
| `agent` | `AgentConfig` | `AgentConfig()` | Agent loop behaviour (tool calls, timeout) |
| `streaming` | `StreamingConfig` | `StreamingConfig()` | SSE heartbeat and max duration settings |
| `queue` | `QueueConfig` | `QueueConfig()` | Workflow queue backend configuration |
| `rate_limits` | `RateLimitConfig` | `RateLimitConfig()` | Per-session and global rate limits |
| `budgets` | `BudgetConfig` | `BudgetConfig()` | Token and cost budgets with enforcement |
| `dev` | `DevConfig` | `DevConfig()` | Development features (UI playground, logging, reload) |
| `plugins` | `list[PluginSetup \| str]` | `None` | Plugin setup callables or module path strings. See [Plugins](#11-plugins). |

**Example:**

```python
from yomai import Yomai

app = Yomai()
```

---

### `Yomai.agent(path, tools=None, *, system="", api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a streaming agent endpoint (POST). The decorated function runs **before** the LLM call and can validate input, load data, or return a dict to dynamically override the system prompt, inject context, or replace the user message.

```python
@app.agent("/chat")
async def chat(message: str, session_id: str):
    return  # LLM call and SSE streaming handled automatically
```

**Handler return value:**

If the handler returns a `dict`, the following keys are used to override the agent's runtime behavior:

| Key | Type | Effect |
|-----|------|--------|
| `system` | `str` | Replaces the static `system=` prompt. Use for dynamic personalization. |
| `context` | `str` | Text prepended above the user's message (separated by `---`). Use for injected facts. |
| `message` | `str` | Fully replaces the user's message. |

Return `None` (or don't return) to keep the static `system=` unchanged.

```python
@app.agent("/support", system="You are helpful.", tools=[faq])
async def support(message: str, session_id: str):
    user = db.get_user(session_id)
    return {
        "system": f"You are support. Customer: {user['name']} ({user['plan']}).",
        "context": f"Profile: name={user['name']}, orders={user['orders']}",
    }
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | *(required)* | URL path for the endpoint (must start with `/`) |
| `tools` | `list[ToolFunction]` | `None` | List of `@tool`-decorated functions available to the LLM |
| `system` | `str` | `""` | System prompt injected at the top of each LLM conversation |
| `api_key` | `str` | `None` | Per-route API key override (falls back to `dev.api_key`) |
| `tags` | `list[str]` | `None` | OpenAPI tags |
| `summary` | `str` | `None` | Short endpoint summary for OpenAPI |
| `description` | `str` | `None` | Longer endpoint description for OpenAPI |
| `deprecated` | `bool` | `False` | Mark the endpoint as deprecated |
| `cors` | `dict` | `None` | Per-route CORS configuration (`allow_origins`, `allow_credentials`, `allow_methods`, `allow_headers`) |
| `dependencies` | `list[Depends]` | `None` | List of `Depends` callables to run before the handler |

**Handler function can receive these injected parameters:**

| Injected name | Type | Description |
|---------------|------|-------------|
| `message` | `str` | The user's message from the JSON body (`{"message": "..."}`) |
| `session_id` | `str` | Auto-generated UUID or from the `X-Session-Id` header |
| `request` | `Request` | The raw Starlette `Request` object |
| *body fields* | *any* | Additional JSON body fields are extracted and coerced to handler parameter types |
| *path params* | *str* | Parameters from the URL path (e.g., `{org_id}`) |

**Example with tools and path parameters:**

```python
from yomai import tool

@tool
def get_weather(city: str) -> str:
    return f"Weather in {city}: sunny, 22°C"

@app.agent("/org/{org_id}/chat", tools=[get_weather], system="You are a helpful assistant")
async def org_chat(message: str, session_id: str, org_id: str, context: str = "") -> None:
    print(f"Chat in org {org_id} with context: {context}")
```

---

### `Yomai.workflow(path, *, mode="stream", api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a workflow endpoint. The function receives a `WorkflowRunner` instance, plus path parameters, body fields, and an optional `request`.

```python
@app.workflow("/process")
async def process(runner, document: str) -> str:
    result = await runner.step("analyze", some_agent, document)
    return result
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | *(required)* | URL path for the endpoint |
| `mode` | `str` | `"stream"` | Execution mode: `"stream"` (SSE streaming) or `"async"` (job-based, returns 202 with job ID) |
| `api_key` | `str` | `None` | Per-route API key override |
| `tags` | `list[str]` | `None` | OpenAPI tags |
| `summary` | `str` | `None` | OpenAPI summary |
| `description` | `str` | `None` | OpenAPI description |
| `deprecated` | `bool` | `False` | Mark as deprecated |
| `cors` | `dict` | `None` | Per-route CORS config |
| `dependencies` | `list[Depends]` | `None` | Dependencies to run before the handler |

**Handler function can receive these injected parameters:**

| Injected name | Type | Description |
|---------------|------|-------------|
| `runner` | `WorkflowRunner` | Runner with `step()`, `parallel()`, `cancelled()`, `raise_if_cancelled()` |
| `request` | `Request` | The raw Starlette `Request` object |
| *body fields* | *any* | JSON body fields coerced to handler parameter types |
| *path params* | *str* | URL path parameters |

---

### `Yomai.get(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a non-streaming GET endpoint. The function can receive `request`, path parameters, query parameters, and `session_id` (from the `X-Session-Id` header).

```python
@app.get("/sessions/{session_id}")
async def get_session(request, session_id: str) -> dict:
    return {"session": session_id}
```

---

### `Yomai.delete(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a non-streaming DELETE endpoint. Same parameter injection as `get()` (request, path params, query params, session_id).

```python
@app.delete("/sessions/{session_id}")
async def clear_session(request, session_id: str) -> dict:
    return {"cleared": session_id}
```

---

### `Yomai.put(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a non-streaming PUT endpoint for full resource replacement. Receives `request`, path params, `session_id`, JSON body fields.

---

### `Yomai.patch(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a non-streaming PATCH endpoint for partial resource updates. Receives `request`, path params, `session_id`, JSON body fields.

---

### `Yomai.head(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers a HEAD endpoint (e.g., for checking if a session exists). Receives `request` and path params.

---

### `Yomai.options(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=False, cors=None, dependencies=None)`

Decorator that registers an OPTIONS endpoint for CORS preflight handling.

---

### `Yomai.on(name)`

Register a lifecycle hook handler.

```python
@app.on("job.succeeded")
async def on_job_done(event: HookEvent):
    print(f"Job {event.payload['job_id']} completed on {event.payload['route']}")
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Hook event name (see [Hook Events](#44-hook-events-reference)) |

**Returns:** a decorator that registers the handler function.

---

### `Yomai.include_router(group)`

Include a `RouteGroup`, registering all its agents, workflows, and GET handlers under the group's prefix.

```python
group = RouteGroup(prefix="/api/v1", tags=["v1"])
app.include_router(group)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `group` | `RouteGroup` | A route group with pre-registered routes |

---

### `Yomai.add_middleware(middleware_class, **kwargs)`

Add a Starlette-compatible middleware class to the application.

```python
app.add_middleware(SomeMiddleware, some_option=True)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `middleware_class` | `type` | A Starlette middleware class |
| `**kwargs` | `Any` | Keyword arguments forwarded to the middleware constructor |

---

### `Depends`

Dependency injection callable used for route-level auth, rate-limiting, or any pre-handler logic.

**Import:** `from yomai import Depends`

```python
class Depends:
    def __init__(
        self,
        callable: Callable[..., Any],
        *,
        use_cache: bool = True,
    ) -> None:
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `callable` | `Callable[..., Any]` | *(required)* | A callable that receives the `Request` and can return any value |
| `use_cache` | `bool` | `True` | Whether to cache the result across multiple injections |

**Example:**

```python
from yomai import Depends

def verify_api_key(request):
    key = request.headers.get("X-API-Key")
    if key != "secret":
        raise Exception("Invalid API key")
    return key

@app.agent("/secure-chat", dependencies=[Depends(verify_api_key)])
async def secure_chat(message: str, session_id: str) -> None:
    ...
```

---

### `RouteGroup`

Group agents, workflows, and GET routes under a shared prefix with shared configuration (tags, CORS, middleware, deprecation status).

**Import:** `from yomai import RouteGroup`

```python
class RouteGroup:
    def __init__(
        self,
        prefix: str = "",
        *,
        tags: list[str] | None = None,
        middleware: list[tuple[type[Any], dict[str, Any]]] | None = None,
        cors: dict[str, Any] | None = None,
        deprecated: bool = False,
    ) -> None:
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prefix` | `str` | `""` | Path prefix for all routes in the group (must start with `/` if non-empty) |
| `tags` | `list[str]` | `None` | Shared OpenAPI tags applied to all routes |
| `middleware` | `list[tuple[type, dict]]` | `None` | Middleware classes and kwargs applied to the group |
| `cors` | `dict` | `None` | Shared CORS config (`allow_origins`, `allow_credentials`, `allow_methods`, `allow_headers`) |
| `deprecated` | `bool` | `False` | Mark all routes in the group as deprecated |

**Methods:**

- `RouteGroup.agent(path, tools=None, *, system="", api_key=None, tags=None, summary=None, description=None, deprecated=None, cors=None, dependencies=None)` — Same signature as `Yomai.agent()`. The `path` is relative to the group prefix.
- `RouteGroup.workflow(path, *, mode="stream", api_key=None, tags=None, summary=None, description=None, deprecated=None, cors=None, dependencies=None)` — Same signature as `Yomai.workflow()`.
- `RouteGroup.get(path, *, api_key=None, tags=None, summary=None, description=None, deprecated=None, cors=None, dependencies=None)` — Same signature as `Yomai.get()`.

**Example:**

```python
api = RouteGroup(prefix="/api/v1", tags=["v1"], deprecated=False)
app.include_router(api)
```

---

## 2. Configuration Models

All configuration models are Pydantic `BaseModel` subclasses. They support construction from keyword arguments, dictionaries, and environment variable auto-population.

**Import:** `from yomai.config import LLMConfig, MemoryConfig, AgentConfig, StreamingConfig, QueueConfig, RateLimitConfig, BudgetConfig, DevConfig`

### `LLMConfig`

Controls the LLM provider, model, and cost tracking.

```python
class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 1024
    cost_per_token: dict[str, float] = {"input": 0.000003, "output": 0.000015}
    strip_reasoning: bool = False
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `provider` | `"anthropic" \| "openai"` | `"anthropic"` | LLM API provider |
| `model` | `str` | `"claude-sonnet-4-20250514"` | Model name (auto-changes to `"gpt-4o-mini"` when provider is `"openai"`) |
| `api_key` | `str` | `""` | API key (falls back to `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` env var) |
| `base_url` | `str \| None` | `None` | Custom API base URL (falls back to `ANTHROPIC_BASE_URL` or `OPENAI_BASE_URL` env var) |
| `max_tokens` | `int` | `1024` | Maximum tokens per LLM response |
| `cost_per_token` | `dict[str, float]` | `{"input": 3e-6, "output": 1.5e-5}` | Per-token cost estimates for budget tracking |
| `strip_reasoning` | `bool` | `False` | If `True`, strips `<think>...</think>` blocks from LLM output |

---

### `MemoryConfig`

Controls the session memory backend.

```python
class MemoryConfig(BaseModel):
    backend: Literal["dict", "sqlite", "redis"] = "sqlite"
    ttl_hours: int = 24
    max_messages: int = 20
    db_path: str = "yomai_sessions.db"
    url: str | None = None
    prefix: str = "yomai:memory"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | `"dict" \| "sqlite" \| "redis"` | `"sqlite"` | Memory storage backend |
| `ttl_hours` | `int` | `24` | Hours before session history expires (0 = never) |
| `max_messages` | `int` | `20` | Maximum message pairs retained per session (0 = unlimited) |
| `db_path` | `str` | `"yomai_sessions.db"` | SQLite database file path (only for `sqlite` backend) |
| `url` | `str \| None` | `None` | Redis connection URL (auto-set from `REDIS_URL` for `redis` backend) |
| `prefix` | `str` | `"yomai:memory"` | Redis key prefix (only for `redis` backend) |

---

### `AgentConfig`

Controls agent loop behaviour.

```python
class AgentConfig(BaseModel):
    max_tool_calls: int = 10
    timeout_secs: int = 120
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_tool_calls` | `int` | `10` | Maximum number of tool-calling iterations before the agent stops |
| `timeout_secs` | `int` | `120` | Maximum total time (seconds) for an agent request before timing out |

---

### `StreamingConfig`

Controls SSE streaming behaviour.

```python
class StreamingConfig(BaseModel):
    heartbeat_secs: int = 15
    max_duration_secs: int = 300
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `heartbeat_secs` | `int` | `15` | Seconds between SSE heartbeat pings |
| `max_duration_secs` | `int` | `300` | Maximum total duration (seconds) for a workflow stream before timeout |

---

### `QueueConfig`

Controls workflow job queue behaviour.

```python
class QueueConfig(BaseModel):
    backend: Literal["none", "inline", "swiftq"] = "none"
    url: str | None = None
    signing_key: str | None = None
    prefix: str = "yomai:swiftq"
    default_queue: str = "default"
    retries: int = 0
    retry_delay_secs: float = 0.0
    timeout_secs: int = 900
    job_ttl_secs: int = 86400
    event_ttl_secs: int = 86400
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | `"none" \| "inline" \| "swiftq"` | `"none"` | Queue backend. `"none"` means no async workflow; `"inline"` runs workflows in-process; `"swiftq"` uses Redis-backed queues |
| `url` | `str \| None` | `None` | Redis URL for the `swiftq` backend (auto-set from `REDIS_URL`) |
| `signing_key` | `str \| None` | `None` | HMAC signing key for workflow payloads |
| `prefix` | `str` | `"yomai:swiftq"` | Redis key prefix for queue entries |
| `default_queue` | `str` | `"default"` | Default queue name |
| `retries` | `int` | `0` | Number of retry attempts on failure |
| `retry_delay_secs` | `float` | `0.0` | Delay between retries in seconds |
| `timeout_secs` | `int` | `900` | Workflow job timeout in seconds |
| `job_ttl_secs` | `int` | `86400` | TTL for job records in Redis (24 hours) |
| `event_ttl_secs` | `int` | `86400` | TTL for stored SSE events in Redis |

---

### `RateLimitConfig`

Controls rate limiting.

```python
class RateLimitConfig(BaseModel):
    requests_per_minute: int | None = None
    max_concurrent_per_session: int | None = None
    tokens_per_day: int | None = None
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `requests_per_minute` | `int \| None` | `None` | Max requests per minute per session (disabled if `None`) |
| `max_concurrent_per_session` | `int \| None` | `None` | Max concurrent requests per session |
| `tokens_per_day` | `int \| None` | `None` | Max tokens per session per day |

---

### `BudgetConfig`

Controls token and cost budgets with enforcement.

```python
class BudgetConfig(BaseModel):
    max_tokens_per_request: int | None = None
    max_tokens_per_session: int | None = None
    max_cost_per_request: float | None = None
    max_cost_per_day: float | None = None
    on_exceeded: Literal["stop", "warn"] = "stop"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_tokens_per_request` | `int \| None` | `None` | Max tokens per single request |
| `max_tokens_per_session` | `int \| None` | `None` | Max total tokens per session |
| `max_cost_per_request` | `float \| None` | `None` | Max estimated cost per request (USD) |
| `max_cost_per_day` | `float \| None` | `None` | Max estimated cost per day (USD) |
| `on_exceeded` | `"stop" \| "warn"` | `"stop"` | Action when a budget is exceeded |

---

### `DevConfig`

Controls development-mode features.

```python
class DevConfig(BaseModel):
    ui: bool = True
    log_usage: bool = True
    reload: bool = True
    api_key: str = ""  # defaults to YOMAI_API_KEY env var
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ui` | `bool` | `True` | Enable the built-in playground UI at `/dev` and `/__yomai__/` |
| `log_usage` | `bool` | `True` | Enable structured usage logging via `StreamLog` |
| `reload` | `bool` | `True` | Enable auto-reload on file changes (CLI only) |
| `api_key` | `str` | `""` | API key for metadata endpoints (defaults to `YOMAI_API_KEY` env var) |

**Example:**

```python
from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, StreamingConfig

app = Yomai(
    llm=LLMConfig(provider="openai", model="gpt-4o", max_tokens=2048),
    memory=MemoryConfig(backend="redis", ttl_hours=48),
    streaming=StreamingConfig(heartbeat_secs=30, max_duration_secs=600),
)
```

---

## 3. Decorators

### `@tool`

Mark a sync or async Python function as LLM-callable. The function's signature, type hints, and docstring are used to auto-generate a JSON Schema tool definition.

**Import:** `from yomai import tool`

```python
@overload
def tool(fn: F, *, cache_ttl: int | None = None) -> F: ...

@overload
def tool(fn: None = None, *, cache_ttl: int | None = None) -> Callable[[F], F]: ...

def tool(fn: F | None = None, *, cache_ttl: int | None = None) -> F | Callable[[F], F]:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fn` | `Callable` | `None` | The function to decorate (can be used with or without parentheses) |
| `cache_ttl` | `int \| None` | `None` | Reserved for V2 — currently emits a `DeprecationWarning` |

The decorator:
1. Auto-generates a JSON Schema from the function signature and type hints.
2. Registers the function in the global tool registry.
3. Attaches `.schema` (the tool schema dict) and `.tool_name` (the function name) attributes.

Type hint to JSON Schema mapping:

| Python type | JSON Schema type |
|-------------|-----------------|
| `str` | `"string"` |
| `int` | `"integer"` |
| `float` | `"number"` |
| `bool` | `"boolean"` |
| `list[T]` | `{"type": "array", "items": ...}` |
| `dict[K, V]` | `{"type": "object"}` |
| `Literal["a", "b"]` | `{"type": "string", "enum": ["a", "b"]}` |
| `Optional[T]` | Same as `T` (nullable) |

**Examples:**

```python
@tool
def get_weather(city: str, units: str = "metric") -> str:
    """Get current weather for a city."""
    return f"Weather in {city}: 22°C"

@tool
async def search_docs(query: str, limit: int = 5) -> list[str]:
    """Search documentation."""
    return [f"Result {i} for {query}" for i in range(limit)]

# Use with an agent
@app.agent("/assistant", tools=[get_weather, search_docs])
async def assistant(message: str, session_id: str) -> None:
    ...
```

---

## 4. Hooks

### `HookEvent`

An immutable dataclass representing a lifecycle hook event.

**Import:** `from yomai import HookEvent`

```python
@dataclass(frozen=True, slots=True)
class HookEvent:
    name: str
    payload: dict[str, Any]
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | The event name (e.g., `"job.succeeded"`) |
| `payload` | `dict[str, Any]` | Event-specific key-value data |

---

### `HookRegistry`

Manages hook handler registration and event emission. An instance is created automatically on each `Yomai` app.

```python
class HookRegistry:
    def on(self, name: str, handler: HookHandler) -> HookHandler: ...

    async def emit(self, name: str, **payload: Any) -> list[dict[str, Any]]: ...

    def emit_background(self, name: str, **payload: Any) -> None: ...

    def pop_failures(self) -> list[dict[str, Any]]: ...
```

| Method | Description |
|--------|-------------|
| `on(name, handler)` | Register a handler for a named event. Returns the handler. |
| `emit(name, **payload)` | Fire all handlers concurrently. **Awaited** — blocks until every handler finishes. Returns failures list. |
| `emit_background(name, **payload)` | Fire all handlers as a background task (fire-and-forget). Does **not** await completion. |
| `pop_failures()` | Return and clear accumulated handler errors across all hook invocations. |

#### `emit()` vs `emit_background()`

| | `await emit()` | `emit_background()` |
|---|---|---|
| **Blocks caller** | Yes, until all handlers done | No, returns immediately |
| **Ordering across hook names** | Guaranteed — handlers for hook A complete before hook B's `emit()` is called | **Not guaranteed** — a slow `agent.start` handler may still be running when `agent.done` fires |
| **Use when** | You need ordering guarantees (e.g., audit trail must be written before next step) | You don't want hook latency to slow the response stream |

```python
# emit() — ordering guaranteed
await app.hooks.emit("agent.start", session_id=sid)
# ... agent runs ...
await app.hooks.emit("agent.done", session_id=sid)
# ↑ agent.done handlers run only after agent.start handlers have finished

# emit_background() — fire-and-forget, no ordering
app.hooks.emit_background("agent.chunk", content="hello")  # <-- may still be running
app.hooks.emit_background("agent.done")                      # <-- when this starts
```

#### Concurrency model

All handlers for a given hook name run **concurrently** via `asyncio.gather`. A slow handler (e.g., Slack webhook at 80ms) does not block fast ones (e.g., metrics counter at 1ms). Handler **order is not guaranteed** — if two handlers must run in sequence, compose them into one handler.

All handlers **must be async** (`async def`). Sync handlers are no longer supported.

---

### Hook Events Reference

Events emitted by the Yomai application and available for handling via `app.on()`:

**Agent lifecycle:**

| Event Name | Payload Keys | When Emitted |
|------------|-------------|--------------|
| `agent.start` | `session_id`, `message` | Agent begins processing a message |
| `agent.chunk` | `session_id`, `content` | Each text chunk from the LLM (high volume — uses `emit_background`) |
| `agent.llm_call` | `session_id`, `iteration`, `tokens_in`, `tokens_out` | Each LLM API call (one per tool-call loop iteration) |
| `agent.tool_call` | `session_id`, `tool_name`, `tool_id`, `args` | A tool is invoked |
| `agent.tool_result` | `session_id`, `tool_name`, `tool_id`, `result`, `duration_ms`, `error` | A tool returns |
| `agent.budget_exceeded` | `session_id`, `reason`, `tokens_in`, `tokens_out` | Token or cost budget limit hit |
| `agent.done` | `session_id`, `tokens_in`, `tokens_out`, `tool_calls`, `iterations` | Agent completes successfully |
| `agent.error` | `session_id`, `error`, `error_type` | Agent fails |

**Request lifecycle:**

| Event Name | Payload Keys | When Emitted |
|------------|-------------|--------------|
| `request.start` | `session_id` | HTTP request begins processing |
| `request.end` | `session_id`, `status` | HTTP request completes (`"ok"`, `"error"`, `"budget_exceeded"`) |

**Stream lifecycle:**

| Event Name | Payload Keys | When Emitted |
|------------|-------------|--------------|
| `stream.start` | `session_id`, `path` | SSE stream opens |
| `stream.end` | `session_id`, `path` | SSE stream closes (disconnect, timeout, or normal completion) |

**Job/workflow lifecycle:**

| Event Name | Payload Keys | When Emitted |
|------------|-------------|--------------|
| `job.queued` | `job_id`, `route` | An async workflow job is queued |
| `job.started` | `job_id`, `route` | An inline workflow job begins execution |
| `job.succeeded` | `job_id`, `route`, `result` | A workflow job completes successfully |
| `job.failed` | `job_id`, `route`, `error` | A workflow job fails |
| `job.cancelled` | `job_id`, `route` | A workflow job is cancelled |
| `workflow.start` | `job_id`, `route` | A workflow begins executing steps |
| `workflow.done` | `job_id`, `route`, `result` | A workflow completes all steps |
| `workflow.failed` | `job_id`, `route`, `error` | A workflow fails |
| `workflow.retrying` | `job_id`, `route`, `attempt` | A workflow retries after failure |
| `error` | `job_id`, `route`, `error` | Any error during request processing |

**Example:**

```python
from yomai import HookEvent

@app.on("agent.start")
async def on_agent_start(event: HookEvent):
    print(f"Agent started: {event.payload['session_id']}")

@app.on("agent.done")
async def on_agent_done(event: HookEvent):
    print(f"Agent done: {event.payload['tokens_in']}→{event.payload['tokens_out']} tokens")

@app.on("agent.tool_call")
async def on_tool(event: HookEvent):
    await send_slack(f"Tool: {event.payload['tool_name']}({event.payload['args']})")

@app.on("stream.start")
async def on_stream_start(event: HookEvent):
    print(f"SSE stream opened on {event.payload['path']}")
```

**Error aggregation:**

```python
@app.on("agent.chunk")
async def flaky_webhook(event: HookEvent):
    if random.random() < 0.1:
        raise RuntimeError("webhook failed")
    await post_to_slack(event.payload["content"])

# After some requests, check for failures:
failures = app.hooks.pop_failures()
for f in failures:
    print(f"Handler '{f['handler']}' failed: {f['error']}")
```


---

## 5. SSE Utilities

Functions for constructing Server-Sent Events (SSE) strings. All SSE data is automatically sanitized (newlines in values are replaced with spaces).

**Import:** `from yomai import format_sse, sse_chunk, sse_done, sse_error, sse_ping, sse_tool_end, sse_tool_start, sse_usage`

### `format_sse(event_type, data)`

Return a correctly formatted SSE string.

```python
def format_sse(event_type: str, data: dict[str, Any]) -> str:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | SSE event type (sanitized automatically) |
| `data` | `dict[str, Any]` | Event data payload (JSON-serialized) |

**Returns:** `str` — formatted SSE string like `"event: type\ndata: {...}\n\n"`

---

### `format_sse_with_id(event_id, event_type, data)`

Return an SSE string with a replay `id` field for event replay.

```python
def format_sse_with_id(event_id: int | str, event_type: str, data: dict[str, Any]) -> str:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_id` | `int \| str` | Replay/sequence ID |
| `event_type` | `str` | SSE event type |
| `data` | `dict[str, Any]` | Event data payload |

**Returns:** `str` — SSE string like `"id: 42\nevent: type\ndata: {...}\n\n"`

---

### `sse_chunk(content)`

Build an SSE text chunk event.

```python
def sse_chunk(content: str) -> str:
```

**Emits:** `event: chunk` with `{"type": "chunk", "content": "..."}`

---

### `sse_tool_start(name, args, id)`

Build an SSE tool start event.

```python
def sse_tool_start(name: str, args: dict[str, Any], id: str) -> str:
```

**Emits:** `event: tool_start` with `{"type": "tool_start", "name": "...", "args": {...}, "id": "..."}`

---

### `sse_tool_end(id, result, duration_ms)`

Build an SSE tool end event.

```python
def sse_tool_end(id: str, result: str, duration_ms: int) -> str:
```

**Emits:** `event: tool_end` with `{"type": "tool_end", "id": "...", "result": "...", "duration_ms": 123}`

---

### `sse_usage(input_tokens, output_tokens, cost_usd)`

Build an SSE usage/statistics event.

```python
def sse_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
```

**Emits:** `event: usage` with `{"type": "usage", "input_tokens": ..., "output_tokens": ..., "cost_usd": ...}`

---

### `sse_done()`

Build an SSE done event signalling stream completion.

```python
def sse_done() -> str:
```

**Emits:** `event: done` with `{"type": "done"}`

---

### `sse_error(message, code="error")`

Build an SSE error event.

```python
def sse_error(message: str, code: str = "error") -> str:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `message` | `str` | *(required)* | Human-readable error message |
| `code` | `str` | `"error"` | Machine-readable error code |

**Emits:** `event: error` with `{"type": "error", "message": "...", "code": "..."}`

---

### `sse_ping()`

Build an SSE ping event (used for heartbeats).

```python
def sse_ping() -> str:
```

**Emits:** `event: ping` with `{}`

---

### `heartbeat(queue, interval_secs=15)`

Async coroutine that periodically sends ping events to a queue.

```python
async def heartbeat(queue: asyncio.Queue[str | None], interval_secs: int = 15) -> None:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `queue` | `asyncio.Queue` | *(required)* | Queue to push ping SSE strings into |
| `interval_secs` | `int` | `15` | Seconds between pings |

---

## 6. Workflow

### `WorkflowRunner`

The runner injected into workflow handler functions. Provides methods for executing agent steps, cancelling, and parallel execution.

**Import:** `from yomai.workflow.runner import WorkflowRunner`

```python
class WorkflowRunner:
    def __init__(
        self,
        sse_queue: asyncio.Queue[str | None],
        session_id: str,
        memory: MemoryBackend,
        app: Yomai,
        job_id: str | None = None,
    ):
```

> **Note:** You never construct a `WorkflowRunner` directly. It is injected into workflow handler functions as the `runner` parameter.

---

### `WorkflowRunner.step(name, agent_fn, input)`

Execute a single agent step within a workflow. This creates an `AgentLoop` with the tools from the given agent function, runs it against the input, saves the result to memory, and returns the final assistant reply.

```python
async def step(self, name: str, agent_fn: Callable[..., Any], input: Any) -> str:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | A human-readable step name (used in SSE events and checkpointing) |
| `agent_fn` | `Callable[..., Any]` | A Yomai agent function (must have `_yomai_tools` attribute) |
| `input` | `Any` | The input to send as the user message to the agent |

**Returns:** `str` — The final assistant reply text.

If a checkpoint exists for this step with the same input hash (and the job is not cancelled), the step is skipped and the cached result is returned.

---

### `WorkflowRunner.parallel(steps)`

Run multiple awaitables concurrently.

```python
async def parallel(self, steps: list[Awaitable[Any]]) -> list[Any]:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `steps` | `list[Awaitable[Any]]` | A list of awaitable coroutines (typically `runner.step()` calls) |

**Returns:** `list[Any]` — List of results in the same order as the input steps.

**Example:**

```python
results = await runner.parallel([
    runner.step("summarize", summarize_agent, doc1),
    runner.step("translate", translate_agent, doc2),
])
```

---

### `WorkflowRunner.cancelled()`

Check whether the current workflow job has been cancelled.

```python
async def cancelled(self) -> bool:
```

**Returns:** `bool` — `True` if the job status is `"cancelled"`, `False` otherwise. Also returns `False` if no `job_id` is set.

---

### `WorkflowRunner.raise_if_cancelled()`

Raise `asyncio.CancelledError` if the current job has been cancelled. Useful for cooperative cancellation between steps.

```python
async def raise_if_cancelled(self) -> None:
```

**Raises:** `asyncio.CancelledError` if the job has been cancelled.

> This is called automatically at the start of each `step()`.

---

### Full Workflow Example

```python
from yomai import Yomai, tool

@tool
def extract_keywords(text: str) -> str:
    return f"Keywords: AI, Yomai, framework"

@tool
def generate_summary(text: str) -> str:
    return f"Summary of: {text[:50]}..."

@app.agent("/keywords", tools=[extract_keywords])
async def keywords_agent(message: str, session_id: str) -> None:
    ...

@app.agent("/summarize", tools=[generate_summary])
async def summarize_agent(message: str, session_id: str) -> None:
    ...

@app.workflow("/process-document")
async def process_document(runner, document: str) -> str:
    await runner.raise_if_cancelled()

    # Run steps in parallel
    keywords_result, summary_result = await runner.parallel([
        runner.step("extract-keywords", keywords_agent, document),
        runner.step("generate-summary", summarize_agent, document),
    ])

    return f"Keywords: {keywords_result}\nSummary: {summary_result}"
```

---

## 7. Memory Backends

### `MemoryBackend` (ABC)

Abstract base class for all Yomai memory backends. You can subclass this to implement custom storage.

**Import:** `from yomai.memory import MemoryBackend`

```python
class MemoryBackend(ABC):
    @abstractmethod
    async def load(self, session_id: str) -> list[Message]: ...

    @abstractmethod
    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None: ...

    @abstractmethod
    async def clear(self, session_id: str) -> None: ...
```

| Method | Description |
|--------|-------------|
| `load(session_id)` | Return the conversation history as a list of message dicts (`list[dict]` with `role` and `content` keys) |
| `save(session_id, user_message, assistant_reply)` | Append a user message and assistant reply to the session history |
| `clear(session_id)` | Delete all history for the given session |

**Message format:**

```python
# Each message is a dict:
{"role": "user", "content": "Hello"}
{"role": "assistant", "content": "Hi there!"}
```

---

### `DictMemory`

In-process, non-persistent memory backend. Suitable for development, testing, and small single-process deployments.

**Import:** `from yomai.memory import DictMemory`

```python
class DictMemory(MemoryBackend):
    def __init__(self, max_messages: int = 20, ttl_hours: int = 24) -> None:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_messages` | `int` | `20` | Maximum message pairs retained per session (0 = unlimited) |
| `ttl_hours` | `int` | `24` | Session TTL in hours (0 = never expire) |

**Notes:**
- Thread-safe via `asyncio.Lock`.
- Data is lost on process restart.
- If a system message is present and `max_messages` is exceeded, the system message is preserved and the oldest non-system messages are trimmed.

---

### `SqliteMemory`

SQLite-backed persistent memory. Survives process restarts.

**Import:** `from yomai.memory import SqliteMemory`

```python
class SqliteMemory(MemoryBackend):
    def __init__(self, db_path: str = "yomai_sessions.db", max_messages: int = 20, ttl_hours: int = 24) -> None:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `db_path` | `str` | `"yomai_sessions.db"` | Path to the SQLite database file |
| `max_messages` | `int` | `20` | Maximum message pairs per session |
| `ttl_hours` | `int` | `24` | Session TTL in hours |

**Notes:**
- Uses WAL journal mode for better concurrency.
- Expired sessions are cleaned on every `load()` or `save()` call.
- Same truncation behaviour as `DictMemory` (preserves system message).

---

### `RedisMemory`

Redis-backed memory for horizontally scaled deployments.

**Import:** `from yomai.memory import RedisMemory`

```python
class RedisMemory(MemoryBackend):
    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        *,
        max_messages: int = 20,
        ttl_hours: int = 24,
        prefix: str = "yomai:memory",
        client: Any | None = None,
    ) -> None:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | `str` | `"redis://localhost:6379/0"` | Redis connection URL |
| `max_messages` | `int` | `20` | Maximum message pairs per session |
| `ttl_hours` | `int` | `24` | Session TTL in hours (uses Redis `EX` key expiry) |
| `prefix` | `str` | `"yomai:memory"` | Redis key prefix |
| `client` | `Any` | `None` | Pre-configured `redis.asyncio.Redis` client (optional) |

**Notes:**
- Requires the `redis` package (`pip install redis>=5`).
- Lazy client initialization (created on first access).
- Keys follow the pattern `{prefix}:sessions:{session_id}`.
- TTL is enforced via Redis key expiry.

---

## 8. Testing

### `YomaiTestClient`

An async test client for making requests to a Yomai app without starting a server. Uses `httpx.ASGITransport` under the hood.

**Import:** `from yomai.testing import YomaiTestClient`

```python
class YomaiTestClient:
    def __init__(self, app: Yomai) -> None:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `app` | `Yomai` | A configured Yomai application instance |

---

#### `YomaiTestClient.stream(path, message, session_id=None, extra_body=None)`

Send a message to an agent endpoint and get text chunks back.

```python
async def stream(
    self,
    path: str,
    message: str,
    session_id: str | None = None,
    extra_body: dict[str, Any] | None = None,
) -> list[str]:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str` | *(required)* | Endpoint path (e.g., `"/chat"`) |
| `message` | `str` | *(required)* | The user message |
| `session_id` | `str` | `None` | Optional session ID (sent as `X-Session-Id` header) |
| `extra_body` | `dict` | `None` | Additional JSON body fields |

**Returns:** `list[str]` — List of text chunk content strings.

---

#### `YomaiTestClient.call(path, message, session_id=None, extra_body=None)`

Send a message and get the concatenated full response text.

```python
async def call(
    self,
    path: str,
    message: str,
    session_id: str | None = None,
    extra_body: dict[str, Any] | None = None,
) -> str:
```

**Returns:** `str` — Full concatenated response text.

---

#### `YomaiTestClient.get_events(path, message, session_id=None, extra_body=None)`

Send a message and get all parsed SSE events.

```python
async def get_events(
    self,
    path: str,
    message: str,
    session_id: str | None = None,
    extra_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
```

**Returns:** `list[dict[str, Any]]` — List of parsed SSE event dicts (with `type` and event-specific fields).

---

#### `YomaiTestClient.post_json(path, data, headers=None)`

Send a raw JSON POST request and get the `httpx.Response`.

```python
async def post_json(
    self,
    path: str,
    data: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
```

---

### `mock_llm`

A context manager that replaces the LLM provider streaming with deterministic, scripted turns. No real API calls are made.

**Import:** `from yomai.testing import mock_llm`

```python
@contextmanager
def mock_llm(
    responses: list[str | MockToolCall | list[str | MockToolCall]] | None = None,
) -> Iterator[None]:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `responses` | `list` | `None` | Predefined turns. Each turn is a `str`, `MockToolCall`, or a list of items. `Done` is auto-emitted at the end of each turn. |

**`MockToolCall` dataclass:**

```python
@dataclass(slots=True)
class MockToolCall:
    name: str
    args: dict[str, Any]
    id: str = "mock-tool-1"
```

**Example:**

```python
import pytest
from yomai.testing import YomaiTestClient, mock_llm, MockToolCall

@pytest.mark.asyncio
async def test_agent():
    client = YomaiTestClient(app)
    with mock_llm(["Hello! How can I help?"]):
        response = await client.call("/chat", "Hi there")
        assert "Hello" in response

@pytest.mark.asyncio
async def test_agent_with_tool():
    with mock_llm([
        MockToolCall(name="get_weather", args={"city": "Paris"}),
        "The weather in Paris is sunny."
    ]):
        events = await client.get_events("/chat", "What's the weather?")
        tool_events = [e for e in events if e["type"] == "tool_start"]
        assert len(tool_events) == 1
```

---

### `capture_tools`

A context manager that intercepts tool calls and returns a fixed result without executing real tool functions. Captured calls are recorded in a list.

**Import:** `from yomai.testing import capture_tools, CapturedToolCall`

```python
@contextmanager
def capture_tools(return_value: str = "mocked tool result") -> Iterator[list[CapturedToolCall]]:
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `return_value` | `str` | `"mocked tool result"` | The fixed string result returned for every captured tool call |

**`CapturedToolCall` dataclass:**

```python
@dataclass(slots=True)
class CapturedToolCall:
    name: str
    args: dict[str, Any]
    result: str | None = None
    duration_ms: int = 0
```

**Example:**

```python
with capture_tools(return_value="sunny, 22°C") as calls:
    with mock_llm([
        MockToolCall(name="get_weather", args={"city": "Berlin"}),
        "The weather is nice."
    ]):
        await client.call("/chat", "Weather in Berlin?")

assert len(calls) == 1
assert calls[0].name == "get_weather"
assert calls[0].args == {"city": "Berlin"}
assert calls[0].result == "sunny, 22°C"
```

---

### `parse_sse`

Parse a raw SSE string into a list of event dicts.

```python
def parse_sse(raw: str) -> list[dict[str, Any]]:
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `raw` | `str` | Raw SSE text from a response body |

**Returns:** `list[dict[str, Any]]` — Parsed events. Ping events are automatically filtered out.

---

## 9. Environment Variables

All environment variables are accessible via `yomai.env`. They read from `os.environ` on every access so tests can override them by modifying `os.environ` directly.

**Import:** `from yomai import env` or `import yomai.env`

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `YOMAI_ENV` | `str` | `"development"` | Set to `"production"` to hide error details, disable the dev playground, and require an API key for metadata endpoints |
| `YOMAI_HANDLE_SIGTERM` | `str` | `""` | Set to `"1"` to enable graceful shutdown on SIGTERM |
| `YOMAI_APP_TITLE` | `str` | `"Yomai Agent API"` | Title used in the OpenAPI schema and playground UI |
| `YOMAI_API_KEY` | `str` | `""` | API key required for `/__yomai__/*` metadata endpoints in production |
| `ANTHROPIC_API_KEY` | `str` | `""` | API key for the Anthropic provider |
| `ANTHROPIC_BASE_URL` | `str \| None` | `None` | Custom base URL for Anthropic-compatible endpoints |
| `OPENAI_API_KEY` | `str` | `""` | API key for the OpenAI provider |
| `OPENAI_BASE_URL` | `str \| None` | `None` | Custom base URL for OpenAI-compatible endpoints |
| `REDIS_URL` | `str` | `"redis://localhost:6379/0"` | Default Redis connection URL used by memory, queue, jobs, and rate-limiter backends |

**Access pattern:**

```python
import os
from yomai import env

# Read at module level
print(env.YOMAI_ENV)  # "development"

# Override in tests
os.environ["YOMAI_ENV"] = "production"
assert env.YOMAI_ENV == "production"
```

---

## 10. Jobs

### `JobRecord`

A dataclass representing a workflow job.

**Import:** `from yomai.jobs import JobRecord`

```python
@dataclass(slots=True)
class JobRecord:
    id: str
    route: str
    status: JobStatus = "queued"
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    attempts: int = 0
    result: Any = None
    error: str | None = None
    stream_url: str | None = None
    status_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | *(required)* | Unique job identifier (e.g., `"job_a1b2c3..."`) |
| `route` | `str` | *(required)* | The workflow endpoint path |
| `status` | `JobStatus` | `"queued"` | Current status (see below) |
| `created_at` | `datetime` | `utcnow()` | When the job was created |
| `started_at` | `datetime \| None` | `None` | When execution started |
| `finished_at` | `datetime \| None` | `None` | When execution ended |
| `attempts` | `int` | `0` | Number of execution attempts |
| `result` | `Any` | `None` | The workflow result (if succeeded) |
| `error` | `str \| None` | `None` | Error message (if failed) |
| `stream_url` | `str \| None` | `None` | URL for streaming SSE events |
| `status_url` | `str \| None` | `None` | URL for polling job status |
| `metadata` | `dict[str, Any]` | `{}` | Arbitrary metadata |

**`JobStatus` type:**

```python
JobStatus = Literal["queued", "running", "retrying", "succeeded", "failed", "cancelled", "expired"]
```

---

### `JobStore` (Protocol)

Protocol defining the interface for job storage backends.

**Import:** `from yomai.jobs import JobStore`

```python
class JobStore(Protocol):
    async def create(self, record: JobRecord) -> JobRecord: ...

    async def get(self, job_id: str) -> JobRecord | None: ...

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: object = None,
        error: str | None = None,
    ) -> JobRecord | None: ...

    async def list(self) -> Iterable[JobRecord]: ...
```

| Method | Description |
|--------|-------------|
| `create(record)` | Store a new job record and return it |
| `get(job_id)` | Retrieve a job by ID (returns `None` if not found) |
| `update_status(job_id, status, *, result, error)` | Update the status and optionally the result/error of a job |
| `list()` | Return all stored job records |

**Built-in implementations:**

- **`InMemoryJobStore`** — In-process dict-based store. Not persisted.
- **`RedisJobStore`** — Redis-backed store for production use. Constructor: `RedisJobStore(url, *, prefix="yomai", ttl_secs=86400, client=None)`.

---

### Built-in Metadata Endpoints

When using a `Yomai` app, the following metadata endpoints are automatically registered (all under `/__yomai__/`):

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/__yomai__/health` | Health check — returns `{"status": "ok", "version": "0.1.0"}` |
| `GET` | `/__yomai__/routes` | List all registered routes with metadata |
| `GET` | `/__yomai__/openapi.json` | OpenAPI 3.x specification |
| `GET` | `/__yomai__/jobs/{job_id}` | Get status of a specific workflow job |
| `GET` | `/__yomai__/jobs/{job_id}/stream` | Stream SSE events for a workflow job (supports `Last-Event-ID` for replay) |
| `POST` | `/__yomai__/jobs/{job_id}/cancel` | Cancel a running workflow job |
| `GET` | `/__yomai__/metrics` | App metrics (active connections, job counts by status, request counts) |
| `GET` | `/dev` or `/__yomai__/` | Playground UI (dev mode only) |

In production (`YOMAI_ENV=production`), all metadata endpoints require `Authorization: Bearer <api_key>`.

---

## 11. Plugins

Plugins are callables `setup(app: Yomai) -> None` that register hooks, middleware, or custom backends when the app starts.

### `Yomai(plugins=[...])`

Pass a list of setup callables or module path strings:

```python
app = Yomai(
    plugins=[
        my_setup_function,              # direct callable
        "my_package.monitoring:setup",  # module path
    ],
)
```

Each plugin receives the fully-initialized `Yomai` instance.

### `@plugin` decorator

```python
from yomai import plugin

@plugin
def setup(app):
    app.hooks.on("agent.start", on_start)
```

Decorated functions are appended to `yomai.plugins._registry`. Load all registered plugins:

```python
from yomai.plugins import _registry
app = Yomai(plugins=list(_registry))
```

### What plugins can do

| Action | Code |
|--------|------|
| Register hooks | `app.hooks.on("agent.start", handler)` |
| Add middleware | `app.add_middleware(CORSMiddleware, ...)` |
| Modify config | `app.config.agent.max_tool_calls = 5` |
| Register routes | `app.agent("/health")(handler)` |

### OpenTelemetry plugin

Bundled at `yomai.contrib.opentelemetry`:

```python
from yomai.contrib.opentelemetry import setup as otel

app = Yomai(plugins=[otel])
```

Creates spans for `agent.run`, `tool.{name}`, and `llm.call` with attributes for tokens, duration, session ID, and errors. Requires `pip install opentelemetry-api opentelemetry-sdk`.

### Writing a plugin

```python
# slack_notifier.py
from yomai import Yomai, HookEvent

def setup(app: Yomai) -> None:
    async def on_agent_error(event: HookEvent) -> None:
        await slack.post(f"Agent failed: {event.payload}")

    app.hooks.on("agent.error", on_agent_error)
```

```python
# main.py
from slack_notifier import setup

app = Yomai(plugins=[setup])
```

No plugin manifest, no YAML, no entry points — just `def setup(app): ...`.

---

## Top-Level Package Exports

For convenience, the following names are available from `from yomai import ...`:

```python
from yomai import (
    Yomai,            # Main application class
    tool,             # @tool decorator
    plugin,           # @plugin decorator
    Depends,          # Dependency injection
    RouteGroup,       # Route grouping
    HookEvent,        # Hook event dataclass
    PluginSetup,      # Plugin type alias
    # SSE utilities
    format_sse,
    sse_chunk,
    sse_done,
    sse_error,
    sse_ping,
    sse_tool_end,
    sse_tool_start,
    sse_usage,
)
```
