# Yomai Framework Specification

> A modern Python web framework for building agentic HTTP APIs.  
> FastAPI-style ergonomics. Streaming-first. Agent-aware from the ground up.

---

## Table of Contents

1. [Vision & Goals](#1-vision--goals)
2. [Core Concepts](#2-core-concepts)
3. [Architecture](#3-architecture)
4. [V1 Specification](#4-v1-specification)
5. [V2 Specification](#5-v2-specification)
6. [Beyond V2](#6-beyond-v2)
7. [Developer Experience](#7-developer-experience)
8. [Performance & Scaling](#8-performance--scaling)
9. [Limitations & Mitigations](#9-limitations--mitigations)
10. [Playground](#10-playground)
11. [Frontend Integration](#11-frontend-integration)
12. [File Structure](#12-file-structure)
13. [Versioning & Roadmap](#13-versioning--roadmap)

---

## 1. Vision & Goals

### What Yomai Is

Yomai is a Python web framework purpose-built for serving AI agents via HTTP. It brings the ergonomics of FastAPI to the world of agentic workflows — where LLMs call tools, maintain memory, and stream responses over time.

A developer who knows FastAPI can be productive in Yomai in under 10 minutes. A developer who has never built an agent can ship their first one in the same time.

### What It Is Not

Yomai is not an agent orchestration library (like LangGraph or CrewAI). It does not define how agents think. It defines how agents are served, streamed, and coordinated over HTTP.

### Core Design Principles

**1. Agents are first-class citizens**  
Routing, streaming, tool calling, and memory are built into the framework — not bolted on.

**2. Streaming by default**  
Every agent endpoint streams. Users see output as it is produced. A thin `.call()` convenience method collects the stream and returns the final text for server-to-server use cases — it is not a separate mode.

**3. Minimal surface area**  
The entire public API fits on one page. Developers only touch: `@app.agent`, `@app.workflow`, `@tool`, and config.

**4. Familiar to web developers**  
Decorator-based routing. Type-annotated parameters. Middleware support. If you know Flask or FastAPI, you know Yomai.

**5. Honest about limitations**  
The framework ships with clear documentation of what it does not handle: auth, multi-tenancy, long-running job queues, and LLM non-determinism. These are the developer's responsibility or V2 features.

### Target Developer

Web developers who want to add AI agents to their products — not ML engineers, not researchers. They are comfortable with Python, REST APIs, and async programming. They are not comfortable wiring up SSE streams, tool call loops, and session management from scratch.

---

## 2. Core Concepts

### 2.1 The Agent Model

An agent in Yomai is an async function decorated with `@app.agent`. In V1 the function is a marker/configuration point: it declares the route signature while the framework handles the LLM call, tool loop, memory, and streaming. Future versions may allow user-authored generators, but V1 keeps the handler body optional.

```
User message ──► session loaded ──► LLM called ──► tool calls handled
             ──► chunks streamed ──► session saved ──► connection closed
```

The developer writes none of this plumbing. They write the agent function and the tools it can call.

### 2.2 The Tool Model

A tool is any sync or async Python function decorated with `@tool`. The decorator extracts a JSON schema from the function's type annotations and registers it for use by the LLM. When the LLM decides to call a tool, the framework intercepts the call, runs the function, and feeds the result back to the LLM — transparently.

### 2.3 The Agent Loop

This is the heart of the framework. It runs on every agent request:

```
1. Build prompt from system + memory + new message
2. Call LLM with tools attached
3. Stream text chunks to client
4. If LLM emits a tool call:
     a. Pause stream
     b. Run the tool function
     c. Feed result back to LLM
     d. Continue streaming
5. Repeat until LLM produces no tool call
6. Save exchange to memory
7. Close stream
```

The loop enforces a hard limit on tool calls per request (default: 10) to prevent runaway LLM behaviour.

### 2.4 The Workflow Model

A workflow is a sequence of agent steps with logic between them, defined in Python code. The framework orchestrates the steps and streams progress to the client.

Unlike a single agent call — where the LLM decides what to do next — a workflow defines the execution path in code. The LLM handles intelligence within each step. The framework handles sequencing.

**Parallelism scope (important):** `runner.parallel()` runs multiple *workflow steps* concurrently and is available in V1. Parallel execution of multiple *LLM tool calls* within a single agent turn is a V2 feature. These are distinct.

### 2.5 The Memory Model

Memory is per-session conversation history. Every request carries a session ID via the `X-Session-Id` header.

**Session ID generation:** If the header is absent, the framework auto-generates a UUID session ID and returns it in the `X-Session-Id` response header. There is no shared `"default"` session. Callers are responsible for storing and re-sending the session ID on subsequent requests.

**Session ID security:** Session IDs are not authenticated by the framework. Any caller who knows a session ID can read or write to that session. Developers must sign session IDs in middleware before trusting them in production. The README provides a signed session middleware example.

The framework loads history before the agent runs and saves the exchange after it completes.

In V1, memory is an in-process dictionary. In V2, it is a pluggable backend (Redis, Postgres, vector stores).

### 2.6 The SSE Event Schema

All agent and workflow endpoints communicate via Server-Sent Events over HTTP. The schema is the contract between the Python backend and any frontend client.

```
event: chunk
data: {"type": "chunk", "content": "Hello, let me check that"}

event: tool_start
data: {"type": "tool_start", "name": "get_weather", "args": {"city": "Tokyo"}, "id": "t1"}

event: tool_end
data: {"type": "tool_end", "id": "t1", "result": "72°F and sunny", "duration_ms": 142}

event: step_start
data: {"type": "step_start", "name": "classify", "index": 1, "of": null}

event: step_done
data: {"type": "step_done", "name": "classify", "duration_ms": 1200}

event: result
data: {"type": "result", "content": "..."}

event: usage
data: {"type": "usage", "input_tokens": 342, "output_tokens": 89, "cost_usd": 0.0004}

event: done
data: {"type": "done"}

event: error
data: {"type": "error", "message": "Rate limit exceeded", "code": "rate_limited"}
```

**`result` event:** Emitted at the end of a workflow run carrying the workflow's return value. Agents do not emit `result` — their output arrives as a sequence of `chunk` events.

**`cost_usd` field:** Cost is estimated using per-model token prices defined in `LLMConfig.cost_per_token`. These are developer-configurable and default to current Anthropic/OpenAI published rates at the time of the framework release. The field is labelled as an estimate; actual billing may differ.

This schema is stable across V1 and V2. New event types may be added but existing ones will not change. Frontend clients built against V1 continue to work in V2.

---

## 3. Architecture

### 3.1 Layer Model

```
┌─────────────────────────────────────────────────────────┐
│                   HTTP / SSE Layer                       │
│   Routing, request parsing, StreamingResponse, CORS     │
├─────────────────────────────────────────────────────────┤
│                  Agent Runtime Layer                     │
│   Agent loop, tool execution, streaming, error handling │
├─────────────────────────────────────────────────────────┤
│                 Workflow Orchestration Layer             │
│   Step sequencing, parallel execution, progress events  │
├─────────────────────────────────────────────────────────┤
│                  Memory & State Layer                    │
│   Session management, history load/save, backends       │
├─────────────────────────────────────────────────────────┤
│                    LLM Adapter Layer                     │
│   Provider abstraction, streaming, tool schema mapping  │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Request Lifecycle

```
POST /chat  {"message": "What's the weather in Tokyo?"}
X-Session-Id: abc123

  │
  ▼
Router matches /chat → AgentRoute
  │
  ▼
Request parsed: message extracted, session_id from header (auto-generated if absent)
  │
  ▼
Memory loaded: history for session abc123
  │
  ▼
StreamingResponse returned to client (connection stays open)
  │
  ▼
Agent loop starts:
  │  LLM streams "Let me check that for you..."   → chunk events to client
  │  LLM emits tool_call: get_weather("Tokyo")
  │  Framework runs get_weather("Tokyo")           → tool_start / tool_end events
  │  Result fed back to LLM
  │  LLM streams "It's 72°F and sunny in Tokyo!"  → chunk events to client
  │  LLM finishes, no more tool calls
  ▼
done event sent
Memory saved: user + assistant messages appended to session abc123
Connection closed
```

### 3.3 Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| ASGI server | Starlette | Same base as FastAPI; routing, middleware, SSE, WebSocket |
| HTTP server | Uvicorn | Production-grade ASGI runner |
| Validation | Pydantic | Type-safe config and tool schemas |
| LLM (primary) | Anthropic SDK | Claude models; best tool-call support |
| LLM (secondary) | OpenAI SDK | GPT-4o; completed in the final V1 hardening phase |
| Memory V1 | Python dict | Zero dependencies; good for dev |
| Memory V2 | Redis | Persistent, fast, widely supported |
| CLI | Typer | FastAPI-ecosystem; familiar |

---

## 4. V1 Specification

V1 has one job: get a developer from zero to a working, streaming, tool-calling agent in under 10 minutes.

### 4.1 V1 Feature Set

#### 4.1.1 Routing

Six route decorators.

```python
@app.agent(path: str, tools: list | None = None, *, system: str = "", ...)
@app.workflow(path: str, *, mode: str = "stream", ...)
@app.get(path: str, *, summary: str | None = None, ...)
@app.delete(path: str, *, summary: str | None = None, ...)
@app.patch(path: str, *, summary: str | None = None, ...)
@app.put(path: str, *, summary: str | None = None, ...)
@app.head(path: str, ...)
@app.options(path: str, ...)
```

**`@app.agent`** and **`@app.workflow`** register streaming SSE POST endpoints (the primary agents and workflows).

**`@app.get`**, **`@app.delete`**, **`@app.patch`**, **`@app.put`** register non-streaming JSON endpoints for CRUD operations on sessions or other resources.

**`@app.head`** registers a HEAD endpoint (useful for existence checks). **`@app.options`** registers an OPTIONS endpoint (used for CORS preflight).

#### Path Parameters

Path parameters use Starlette-style `{param_name}` syntax:

```python
@app.agent("/chat/{session_id}")
async def chat(message: str, session_id: str):
    pass
```

The `{session_id}` segment is extracted from the URL path and injected as a typed function parameter. Path parameters are distinct from body parameters in OpenAPI generation.

#### Route Metadata

Every route decorator accepts these optional kwargs:

| kwarg | purpose |
|---|---|
| `tags: list[str]` | OpenAPI tag grouping (e.g. `["support", "v2"]`) |
| `summary: str` | Short human-readable summary for OpenAPI |
| `description: str` | Long description for OpenAPI |
| `deprecated: bool` | Marks the route as deprecated in OpenAPI |
| `cors: dict` | Per-route CORS configuration (see below) |
| `dependencies: list[Depends]` | Route-level dependency injection callables |
| `api_key: str` | Route-specific API key override |

#### Per-Route CORS

```python
@app.agent("/chat", cors={
    "allow_origins": ["https://app.example.com"],
    "allow_methods": ["POST"],
    "allow_headers": ["Content-Type", "Authorization"],
    "allow_credentials": True,
})
```

CORS headers are set on every response from that route. CORS is NOT global middleware — each route can have its own origin allowlist.

#### Depends — Route-Level Dependency Injection

`Depends` runs a callable before the route handler and can short-circuit with an error:

```python
def verify_session(request) -> dict:
    session_id = request.headers.get("X-Session-Id", "")
    if not session_id:
        raise HTTPException(401, "Session ID required")
    return {"session_id": session_id}

@app.agent("/chat", dependencies=[Depends(verify_session)])
async def chat(message: str, session_id: str):
    pass
```

The callable receives the Starlette `Request` object. If it raises an exception, the route returns an error response. If it returns a value, the result is discarded — `Depends` is currently a pre-processing hook only (results are not injected into handlers; use `request.state` for shared data).

#### RouteGroup — Grouping and Versioning

`RouteGroup` groups agents and workflows under a shared URL prefix and shared config:

```python
v1 = RouteGroup("/api/v1", tags=["v1"], deprecated=True)

@v1.agent("/chat")
async def chat(message: str, session_id: str):
    pass

@v1.workflow("/search")
async def search(topic: str, runner: WorkflowRunner):
    ...

app.include_router(v1)
# Registers: POST /api/v1/chat, POST /api/v1/search
```

Shared config applied to every route in the group (but overridable per-route):
- `tags` — merged with per-route tags
- `cors` — applied to every route unless overridden
- `deprecated` — applied to every route unless overridden

#### Route Conflicts

Duplicate paths raise a `YomaiRouteError` at startup, not at runtime.

#### System Routes

Three system routes are mounted automatically:
- `GET /__yomai__` — dev playground (dev mode only)
- `GET /__yomai__/health` — health check
- `GET /__yomai__/routes` — introspection endpoint listing all registered routes
- `GET /__yomai__/openapi.json` — OpenAPI 3.1 schema

#### 4.1.2 Agent Decorator

```python
@app.agent("/chat/{session_id}", tools=[get_weather, search_flights], system="You are helpful.")
async def chat_agent(message: str, session_id: str):
    pass  # marker-only handler; framework runs the LLM/tool/memory loop
```

The decorator:
- Registers the path as a POST endpoint (supports path parameters)
- Stores route metadata, tools, and app/config references on the decorated function for workflows and introspection
- Injects `message` from request body automatically
- Injects `session_id` from `X-Session-Id` header; auto-generates a UUID if the header is absent and returns it in the response header
- Injects path parameters (e.g. `session_id` from `/chat/{session_id}`) from the URL path
- Supports custom `system` prompt override
- Wraps the internal agent loop in a `StreamingResponse`
- Emits SSE-formatted events from the internal agent loop
- Emits `done` event on clean completion
- Emits `error` event on exception, even mid-stream
- Cancels the LLM call if the client disconnects

#### 4.1.3 Non-Streaming Route Decorators

```python
# GET — for reading session history or other data
@app.get("/sessions/{session_id}", tags=["sessions"])
async def get_session(session_id: str):
    # Non-streaming JSON response
    return {"session_id": session_id, "message_count": 10}

# DELETE — for clearing sessions
@app.delete("/sessions/{session_id}", tags=["sessions"])
async def delete_session(session_id: str):
    await memory.clear(session_id)
    return {"deleted": session_id}

# PATCH — for partial updates
@app.patch("/sessions/{session_id}", tags=["sessions"])
async def update_session(session_id: str, message: str | None = None):
    return {"updated": session_id}

# PUT — for full replacement
@app.put("/sessions/{session_id}", tags=["sessions"])
async def replace_session(session_id: str, topic: str):
    return {"replaced": session_id, "topic": topic}
```

All non-streaming routes:
- Return JSON responses (not SSE)
- Support path parameters extracted from `{param_name}` in the URL path
- Support query parameters for GET requests
- Support body parameters via JSON request body for PATCH/PUT/DELETE
- Support the same CORS, auth, dependency injection, and metadata options as agents
- Inject `session_id` from `X-Session-Id` header if the handler function has a `session_id` parameter

#### 4.1.3 Tool Decorator

```python
@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return await weather_api.fetch(city)
```

The decorator:
- Marks the function as LLM-callable
- Auto-generates a JSON schema from type annotations using Pydantic
- Supports sync and async functions
- Supports optional parameters with defaults
- Registers the function in the global tool registry

Tools are passed explicitly to agents: `@app.agent("/chat", tools=[get_weather])`. There is no implicit tool discovery. Explicit is better than magic.

#### 4.1.4 Agent Loop

The internal loop that powers every agent request:

```
while iterations < MAX_TOOL_CALLS:
    stream LLM response
    if response contains tool call:
        emit tool_start event
        execute tool function (sequential in V1; parallel in V2)
        emit tool_end event
        append result to messages
        continue loop
    else:
        break
emit done event
```

V1 defaults:
- `max_tool_calls`: 10 per request
- `timeout_secs`: 120 per request
- Tools run sequentially (parallel tool call execution — multiple tools requested by the LLM in a single turn — is V2)

#### 4.1.5 SSE Streaming

Every agent and workflow response is a `text/event-stream` response. Required headers:

```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
Connection: keep-alive
```

`X-Accel-Buffering: no` is critical for Nginx deployments — without it Nginx buffers the stream and the client receives nothing until the response completes.

Heartbeat: a `ping` event is sent every 15 seconds on idle connections to prevent proxy timeouts.

```
event: ping
data: {}
```

**TLS note:** SSE over plain HTTP will be blocked by many browsers and CDNs in production. Terminate TLS at the load balancer or reverse proxy. The framework does not manage TLS directly.

#### 4.1.6 Memory

V1 ships one memory backend: an in-process dictionary.

```python
class DictMemory:
    async def load(self, session_id: str) -> list[dict]
    async def save(self, session_id: str, user_msg: str, reply: str) -> None
    async def clear(self, session_id: str) -> None
```

Sessions are keyed by `X-Session-Id` header. If the header is absent, a UUID session ID is auto-generated and returned to the caller. Memory is not persisted across server restarts in V1.

**Memory growth:** The dict backend has no TTL eviction. Sessions accumulate in memory until the process restarts. In V1, keep the last 20 messages per session to bound growth. The `ttl_hours` config field is accepted but has no effect in V1 — it is reserved for V2 Redis backend eviction.

The `MemoryBackend` abstract base class is published in V1 so developers can implement their own backends before V2 ships the official Redis adapter.

#### 4.1.7 Workflow Decorator and Runner

```python
@app.workflow("/research")
async def research_workflow(topic: str, runner: WorkflowRunner):
    results  = await runner.step("search",    searcher_agent, topic)
    analysis = await runner.step("analyze",   analyst_agent,  results)
    report   = await runner.step("write",     writer_agent,   analysis)
    return report
```

The workflow's return value is emitted to the client as a `result` SSE event immediately before the `done` event.

`WorkflowRunner` methods available in V1:

```python
await runner.step(name: str, agent, input: Any) -> Any
await runner.parallel(steps: list[Coroutine]) -> list[Any]
```

`runner.step` emits `step_start` and `step_done` SSE events automatically. Because V1 workflows are plain Python and may branch dynamically, `step_start.of` is `null` unless a total step count is explicitly known.

`runner.parallel` runs *workflow steps* concurrently using `asyncio.gather`. This is distinct from parallel LLM tool call execution (V2): `runner.parallel` is developer-directed; V2 parallel tool execution is LLM-directed.

Conditional branching and loops are plain Python — no framework primitives needed:

```python
@app.workflow("/support")
async def support_workflow(ticket: str, runner: WorkflowRunner):
    category = await runner.step("classify", classifier, ticket)

    if category == "billing":
        return await runner.step("resolve", billing_agent, ticket)
    else:
        return await runner.step("resolve", tech_agent, ticket)
```

#### 4.1.8 LLM Adapters

V1 ships two adapters behind a common `LLMProvider` abstract base class.

```python
class LLMProvider(ABC):
    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> AsyncIterator[LLMEvent]: ...
```

`LLMEvent` is a union type:
- `TextChunk(content: str)`
- `ToolCall(id: str, name: str, args: dict)`
- `Done()`

Providers shipped in V1:
- `AnthropicProvider` — Claude Sonnet (default model: `claude-sonnet-4-20250514`)
- `OpenAIProvider` — GPT-4o

Provider is set via config:

```python
app = Yomai(llm=LLMConfig(provider="anthropic", model="claude-sonnet-4-20250514"))
```

#### 4.1.9 Configuration

All configuration lives in one object. Every field has a sensible default.

```python
app = Yomai(
    llm = LLMConfig(
        provider        = "anthropic",               # "anthropic" | "openai"
        model           = "claude-sonnet-4-20250514",
        api_key         = env("ANTHROPIC_API_KEY"),
        max_tokens      = 1024,
        cost_per_token  = {                          # used for usage event cost estimates
            "input":  0.000003,
            "output": 0.000015,
        },
    ),
    memory = MemoryConfig(
        backend   = "dict",                          # "dict" only in V1
        ttl_hours = 24,                              # reserved; no effect in V1 dict backend
        max_messages = 20,                           # per-session message cap for dict backend
    ),
    agent = AgentConfig(
        max_tool_calls = 10,
        timeout_secs   = 120,
    ),
    streaming = StreamingConfig(
        heartbeat_secs    = 15,
        max_duration_secs = 300,
    ),
    dev = DevConfig(
        ui        = True,                            # playground enabled
        log_usage = True,                            # token/cost logging
        reload    = True,                            # hot reload
    ),
)
```

#### 4.1.10 Synchronous Call Convenience Method

For server-to-server use cases where streaming is not needed, agents expose a `.call()` method that collects the full stream and returns the final text:

```python
# Streaming (default)
agent.send("What's the weather in Tokyo?")

# Synchronous collect — same underlying stream, convenience wrapper only
result = await yomai_client.call("/chat", message="What's the weather in Tokyo?")
```

`.call()` is available on `YomaiClient` in the frontend packages and on `YomaiTestClient` in the testing module. It does not add a non-streaming server mode.

#### 4.1.11 CLI

Two commands ship in V1:

```bash
yomai new <project-name>
```
Scaffolds a new project with the standard directory structure, a sample agent, a sample tool, and a `requirements.txt`.

```bash
yomai run
```
Starts the development server with hot reload. Prints registered routes, bound address, and a link to the playground. Equivalent to `uvicorn main:app --reload` but with Yomai-aware output.

#### 4.1.12 Testing Utilities

Testing ships in V1. LLM outputs are non-deterministic; testing tool calls and memory behaviour requires deterministic control of the LLM layer.

```python
from yomai.testing import YomaiTestClient, mock_llm, capture_tools

async def test_weather_agent():
    with mock_llm(responses=["It's 72°F in Tokyo"]):
        client = YomaiTestClient(app)
        chunks = await client.stream("/chat", message="Weather in Tokyo?")
        assert "72°F" in "".join(chunks)

async def test_tool_was_called():
    with capture_tools() as calls:
        await client.stream("/chat", message="Weather in Tokyo?")
    assert calls[0].name == "get_weather"
    assert calls[0].args == {"city": "Tokyo"}

async def test_memory_persists():
    sid = "test-session-1"
    await client.stream("/chat", message="My name is Sarah", session_id=sid)
    reply = await client.stream("/chat", message="What's my name?", session_id=sid)
    assert "Sarah" in reply
```

`mock_llm` bypasses all LLM API calls — tests are fast and free.  
`capture_tools` records all tool calls without executing them — tests are deterministic.  
`YomaiTestClient` wraps the ASGI app directly — no server process required.

#### 4.1.13 Error Handling

All errors follow the same pattern: a clear message, the cause, and a fix.

```
YomaiError: Tool schema mismatch in 'get_weather'

  The function returned None but the LLM expected a string.
  Check that your function always returns a value.

  Hint: Add a return type annotation:
    async def get_weather(city: str) -> str:

  Docs: https://yomai.dev/tools#return-types
```

Error categories:
- `YomaiConfigError` — invalid configuration at startup
- `YomaiRouteError` — routing conflicts or invalid path
- `YomaiToolError` — tool schema or execution errors
- `YomaiLLMError` — provider errors (rate limits, auth failures, timeouts)
- `YomaiMemoryError` — memory backend errors

All errors mid-stream are caught and emitted as `error` SSE events. The connection is then closed cleanly. The server never crashes on a per-request error.

#### 4.1.14 Usage Logging

In dev mode, every completed request logs:

```
[12:01:33] POST /chat  session=abc123
           → tool: get_weather(city="Tokyo")    142ms
           → tokens: 342 input / 89 output
           → cost: ~$0.0004 (estimated)
           → total: 2.3s  ✓
```

Usage data is also emitted as a `usage` SSE event so frontend clients can display it. Cost is marked as estimated.

#### 4.1.15 Graceful Shutdown

On `SIGTERM`:
1. Stop accepting new connections
2. Wait up to 30 seconds for active streams to complete
3. Exit cleanly

### 4.2 V1 Explicit Non-Features

The following are out of scope for V1. Attempting to use them raises a helpful `NotImplementedError` pointing to the roadmap.

- Authentication and authorisation — use middleware
- Rate limiting — use middleware
- Redis or any persistent memory backend
- Vector / semantic memory
- Parallel LLM tool call execution (multiple tools in a single agent turn; sequential only in V1)
- Multi-agent coordination
- Async workflow mode (queue-based)
- WebSocket support
- Voice or audio streaming
- Image input to agents
- `yomai deploy` CLI command

### 4.3 V1 Public API Surface

This is the complete list of symbols a developer imports and uses. Nothing else is public API.

```python
from yomai import Yomai, tool, Depends, RouteGroup
from yomai.config import Config, LLMConfig, MemoryConfig, AgentConfig, StreamingConfig, DevConfig
from yomai.memory import MemoryBackend          # ABC for custom backends
from yomai.workflow import WorkflowRunner
from yomai.llm import LLMProvider               # ABC for custom providers
from yomai.events import AgentStartEvent, AgentDoneEvent, ToolEndEvent, ErrorEvent
from yomai.testing import YomaiTestClient, mock_llm, capture_tools
```

### 4.4 V1 Success Metric

V1 ships when a developer can do the following from scratch in under 10 minutes:

```bash
pip install yomai
yomai new my-agent
cd my-agent
yomai run
# open localhost:8000/__yomai__
# type "what's the weather in Tokyo?"
# see streaming response with tool call visible
```

### 4.5 V1 Build Phases

| Phase | Deliverable | Target |
|---|---|---|
| 1 | `@app.agent` + SSE streaming, no tools, no memory | Week 1 |
| 2 | `@tool` decorator + tool call loop | Week 2 |
| 3 | `DictMemory` + session management (auto-generated session IDs) | Week 3 |
| 4 | `@app.workflow` + `WorkflowRunner` + `result` SSE event | Week 3–4 |
| 5 | Testing utilities (`YomaiTestClient`, `mock_llm`, `capture_tools`) | Week 4 |
| 6 | CLI, playground, usage logging, pretty errors | Week 4–5 |
| 7 | OpenAI adapter, heartbeat, disconnect handling, `.call()` method, hardening | Week 5–6 |

---

## 5. V2 Specification

V2 makes Yomai production-ready. It does not change the developer API — code written for V1 runs unchanged in V2.

### 5.1 Redis Memory Backend

```python
app = Yomai(
    memory = MemoryConfig(
        backend   = "redis",
        url       = env("REDIS_URL"),
        ttl_hours = 72,
    )
)
```

The `RedisMemory` backend implements the `MemoryBackend` ABC. Sessions survive server restarts and work across multiple server instances. This is the unlock for horizontal scaling.

Hot cache strategy: an in-process LRU cache sits in front of Redis. Cache hits cost ~0ms. Redis reads are only needed on cache miss (new session or server restart).

### 5.2 Smart Memory: Truncation and Summarisation

Long conversations eventually exceed the LLM context window. V2 adds three strategies, configurable per agent:

**Truncation** (default): Keep the most recent N messages.

**Summarisation**: When history exceeds a token threshold, summarise older messages into a single system note, then continue with recent messages.

**Semantic retrieval**: Store all messages in a vector index. On each request, retrieve the K most relevant past messages rather than the most recent. Requires a vector backend (Qdrant, Pinecone, or pgvector).

```python
@app.agent("/chat", memory_strategy="summarise")
async def chat(message: str, session_id: str):
    ...
```

### 5.3 Parallel Tool Execution

When the LLM requests multiple tools in a single response, V2 runs them concurrently:

```python
# V1: sequential — total time = sum of all tool durations
# V2: parallel  — total time = slowest tool duration

# No code change required — framework detects multiple tool calls automatically
```

### 5.4 Async Workflow Mode (Queue-Based)

Long workflows that risk connection drops or server restarts move to a queue model.

```python
@app.workflow("/research", mode="async")   # one-line change from V1
async def research(topic: str, runner: WorkflowRunner):
    ...
```

Client interaction changes when `mode="async"`:

```
POST /research
→ 202 Accepted
  {"job_id": "job_abc123", "stream_url": "/jobs/job_abc123/stream"}

GET /jobs/job_abc123/stream
→ SSE stream (reconnectable — client can disconnect and reconnect)
```

**Frontend impact:** Async workflows require frontend code changes. The default SSE streaming flow becomes a two-step fetch (POST → poll stream URL). `@yomai/client` handles this automatically when it receives a 202 response; custom clients must be updated.

The SSE stream is reconnectable. If the client drops mid-workflow, it reconnects with the same job ID and receives all events from where it left off.

Infrastructure required: Redis (already in V2 stack) + RQ worker processes.

### 5.5 Workflow Checkpointing

Step results are persisted to Redis as they complete. If the server crashes mid-workflow:
- The job is requeued
- Completed steps are not re-run
- The workflow resumes from the last successful checkpoint

```python
# Transparent to developer — WorkflowRunner handles it internally
result = await runner.step("search", searcher, topic)
# ↑ result is saved to Redis immediately after completion
```

### 5.6 Tool Result Caching

```python
@tool(cache_ttl=300)   # cache result for 5 minutes
async def get_weather(city: str) -> str:
    return await weather_api.fetch(city)
```

Cache key is derived from the tool name and arguments. Cached results bypass the tool function entirely. Cache backend is Redis in V2.

### 5.7 Hooks System

```python
@app.on("agent.start")
async def before_agent(event: AgentStartEvent):
    await analytics.track("agent_start", session_id=event.session_id)

@app.on("tool.end")
async def after_tool(event: ToolEndEvent):
    await db.log(tool=event.tool_name, duration=event.duration_ms)

@app.on("agent.done")
async def after_agent(event: AgentDoneEvent):
    await cost_tracker.record(event.usage)

@app.on("error")
async def on_error(event: ErrorEvent):
    await sentry.capture(event.error)
```

All hook handlers are async. Hooks do not block the response stream — they run as background tasks.

### 5.8 Rate Limiting

Built-in rate limiting at the framework level, per session:

```python
app = Yomai(
    rate_limits = RateLimitConfig(
        requests_per_minute  = 60,
        tokens_per_day       = 100_000,
        max_concurrent       = 3,
    )
)
```

When exceeded:

```
event: error
data: {"type": "error", "code": "rate_limited", "retry_after": 30}
```

### 5.9 Token Budget Management

```python
app = Yomai(
    budgets = BudgetConfig(
        max_tokens_per_request  = 10_000,
        max_tokens_per_session  = 100_000,
        max_cost_per_request    = 0.10,
        on_exceeded             = "stop",    # "stop" | "warn"
    )
)
```

When a request approaches the budget, a warning is injected into the LLM context. When the budget is exceeded, the stream closes with an `error` event.

### 5.10 Metrics Endpoint

```
GET /__yomai__/metrics

{
  "active_connections":    12,
  "requests_total":      4521,
  "requests_last_minute":  18,
  "avg_duration_ms":     3200,
  "tokens_used_today":  450000,
  "cost_today_usd":       4.50,
  "errors_last_hour":       3,
  "tool_calls_total":    8934
}
```

Suitable for ingestion by Prometheus, Datadog, or any metrics platform.

### 5.11 V2 Non-Features (Deferred to V3+)

- WebSocket support
- Voice / audio streaming
- Image input to agents
- Multi-agent coordination protocol
- `yomai deploy` CLI

---

## 6. Beyond V2

These are directional, not committed. They inform architecture decisions made today.

### 6.1 Multi-Agent Coordination

Multiple Yomai services communicating with each other over HTTP. An orchestrator agent calls specialist agents as tools.

```python
# An agent tool that calls another Yomai service
@tool
async def call_booking_agent(details: str) -> str:
    return await yomai_client.call("http://booking-service/agent", details)
```

Because every agent is already an HTTP endpoint with a `.call()` method, this works in V1 without framework changes. V3 formalises it with a typed `AgentClient`, service discovery, and shared tracing.

### 6.2 WebSocket Support

Bidirectional streaming for use cases where the user needs to interrupt or redirect the agent mid-response.

```python
@app.agent("/chat", transport="websocket")
async def chat(message: str, session_id: str, ws: WebSocket):
    ...
```

### 6.3 Long-Running Job Management

For workflows that take hours (report generation, data processing, batch analysis):

- Persistent job store (Postgres)
- Job status polling endpoint
- Email / webhook notification on completion
- Resume from checkpoint after days-long pause

### 6.4 Observability Platform Integration

First-class support for:
- OpenTelemetry traces (one span per agent step, per tool call)
- LangSmith / LangFuse integration for LLM-specific tracing
- Structured JSON logs for ingestion into Datadog, Splunk, Loki

### 6.5 Voice and Audio

SSE stream carries audio chunks alongside text:

```
event: audio_chunk
data: {"type": "audio", "data": "<base64>", "format": "mp3"}
```

### 6.6 Image and Document Input

Agents accept files alongside text messages. The framework handles multipart parsing and forwards content to the LLM in the correct format.

### 6.7 Yomai Deploy

```bash
yomai deploy --platform fly        # Fly.io
yomai deploy --platform railway    # Railway
yomai deploy --platform aws        # AWS ECS
```

Generates platform-specific config, builds a Docker image, and deploys. Manages Redis provisioning for V2 memory backends.

---

## 7. Developer Experience

### 7.1 Type Safety

Full type annotations throughout the public API. Type stubs (`.pyi` files) published for all public symbols. Developers get autocomplete and inline documentation in VSCode and PyCharm without installing additional plugins.

### 7.2 Error Messages

Every error answers three questions:
1. What broke
2. Why it broke
3. How to fix it

Errors include a documentation link. Stack traces are hidden in production and shown in development.

### 7.3 Hot Reload

`yomai run` uses Uvicorn's `--reload` flag. Saving any Python file restarts the server in under a second. Tool schemas, routes, and memory backends are re-registered on restart.

### 7.4 Dev Server Output

```
  Yomai v1.0.0  ·  http://localhost:8000
  Playground  →  http://localhost:8000/__yomai__

  Routes
    POST  /chat        AgentRoute    tools: [get_weather, search_flights]
    POST  /research    WorkflowRoute steps: [search, analyze, write]

  [12:01:33] POST /chat  session=abc123
             ⚙ get_weather(city="Tokyo")  →  "72°F sunny"  142ms
             ✓ 2.3s  ·  342→89 tokens  ·  ~$0.0004 (est.)

  [12:01:40] POST /research  session=def456
             ▸ step 1/3  classify    1.2s  ✓
             ▸ step 2/3  research    4.5s  ✓
             ▸ step 3/3  write       3.1s  ✓
             ✓ 8.8s  ·  ~$0.012 (est.)
```

### 7.5 Middleware

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"])
app.add_middleware(MyAuthMiddleware)
```

Middleware runs before routing. Yomai ships `CORSMiddleware` and `LoggingMiddleware`. Auth, rate limiting, and request ID generation are left to the developer or third-party middleware.

**Auth note:** There is no built-in authentication. The README includes a signed session ID middleware example as the minimum recommended starting point for production deployments.

---

## 8. Performance & Scaling

### 8.1 Key Metrics

| Metric | V1 Target | V2 Target |
|---|---|---|
| Time to first token | < 1s | < 500ms |
| Tool call overhead | < 200ms | < 50ms |
| Memory load time | < 20ms | < 5ms |
| Concurrent streams (1 server) | 100+ | 1000+ |

### 8.2 Why Async Matters

Every agent request spends 90%+ of its time waiting for the LLM. During that wait, asyncio handles other requests. A single Uvicorn process can serve hundreds of simultaneous streaming connections because none of them block a thread.

This is the fundamental reason Yomai is built on asyncio and why sync frameworks (Flask, Django) are unsuitable for agentic workloads.

### 8.3 Scaling Path

```
Stage 1 — Single process
  uvicorn main:app
  Handles ~100 concurrent streams

Stage 2 — Multiple workers
  gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
  Linear scaling with CPU core count

Stage 3 — Horizontal scaling
  Multiple servers behind a load balancer
  Requires Redis memory backend (V2) for stateless servers
  Sticky sessions OR shared Redis — Redis preferred

Stage 4 — Queue-based (V2 async workflows)
  Heavy workflows offloaded to RQ workers
  HTTP servers remain lightweight and fast
  Worker count scales independently of HTTP servers
```

### 8.4 Connection Management

```python
app = Yomai(
    server = ServerConfig(
        max_connections         = 1000,
        max_connections_per_ip  = 10,
        connection_timeout_secs = 300,
        keepalive_interval_secs = 15,
    )
)
```

### 8.5 Client Disconnect Handling

When a client disconnects mid-stream:
1. The framework detects disconnection via `await request.is_disconnected()`
2. The LLM stream is cancelled
3. The session is not saved (incomplete exchange)
4. No error is logged (disconnections are normal)

This prevents wasted LLM tokens and avoids corrupting session memory with partial responses.

---

## 9. Limitations & Mitigations

### 9.1 LLM Non-Determinism

**Problem**: Same input, different output every run. No guaranteed format compliance.

**V1 Mitigation**: Document clearly. Use `mock_llm` in tests to assert tool call behaviour independently of response content. Developers must validate tool return values and handle unexpected LLM output.

**V2 Mitigation**: Structured output mode — force JSON-schema-compliant responses from the LLM for tool results and workflow step outputs.

### 9.2 Context Window Ceiling

**Problem**: Long conversations, large tool results, and multi-step workflows consume tokens. Every LLM has a hard limit.

**V1 Mitigation**: Keep the last 20 messages only (configurable via `max_messages`). Developers must handle this themselves for longer sessions.

**V2 Mitigation**: Configurable memory strategies (truncation, summarisation, semantic retrieval).

### 9.3 Unpredictable Latency

**Problem**: LLM response time varies from 2 to 60+ seconds. HTTP proxies and mobile networks have their own opinions about long connections.

**Mitigation**: Streaming (user sees progress immediately). Heartbeat events (keep-alive every 15s). `X-Accel-Buffering: no` header (prevents Nginx buffering). Configurable timeouts per agent.

### 9.4 Tool Call Loops

**Problem**: LLM can call the same tool repeatedly, burning tokens and never finishing.

**Mitigation**: Hard limit of 10 tool calls per request (configurable). On limit exceeded, the agent sends a user-visible message and the stream closes cleanly.

### 9.5 Mid-Stream Failures

**Problem**: If an error occurs after streaming has started, the client has already received partial data that cannot be retracted.

**Mitigation**: All exceptions are caught and emitted as `error` SSE events before closing the connection. The client always knows whether the stream ended cleanly (`done`) or with an error (`error`).

### 9.6 No Auth or Multi-Tenancy

**Problem**: The framework ships no authentication. Any caller can reach any agent endpoint. Session IDs in headers are not verified.

**Mitigation**: Auth is intentionally out of scope. Developers add auth middleware. The README makes this explicit, provides a signed session ID middleware example, and links to common auth patterns. Session IDs are auto-generated UUIDs — there is no shared `"default"` session to accidentally leak.

### 9.7 Workflow Fragility (V1)

**Problem**: V1 workflows hold all state in memory. A server crash mid-workflow loses all progress.

**V1 Mitigation**: Document clearly. V1 workflows are suitable for tasks under 60 seconds with acceptable restart risk.

**V2 Mitigation**: Async workflow mode with Redis checkpointing.

### 9.8 Invisible Costs

**Problem**: Every LLM call costs money. Runaway loops or misconfigured agents can burn budget silently.

**Mitigation**: Usage logging enabled by default in dev. `usage` SSE events expose token counts and cost estimates (marked as estimates) to the frontend. V2 adds budget caps.

### 9.9 Cost Estimate Staleness

**Problem**: Token pricing changes. `cost_per_token` defaults in `LLMConfig` will go stale over time.

**Mitigation**: Defaults are documented with the date they were last verified. Developers override them in config. Cost fields in SSE events and logs are labelled as estimates.

---

## 10. Playground

### 10.1 What It Is

A single HTML page served at `/__yomai__` in development mode. It is a debugging tool first and a demo tool second. No installation, no build step, no external dependencies.

### 10.2 Features

**Chat interface**: Send messages to any registered agent. Responses stream in real time with word-by-word appearance.

**Agent switcher**: Dropdown lists all registered agents and workflows. Switching starts a new session.

**Tool call panel**: Every tool call is displayed as it happens — name, arguments, result, and duration. Matches the inline indicator in the chat bubble.

**Event log**: Raw SSE events displayed in real time. Toggle between friendly view and raw JSON. Used to verify the SSE schema before wiring up a real frontend.

**Usage bar**: Token counts and cost estimate displayed after each response.

**Session controls**: Session ID displayed at the bottom. New Session button generates a fresh session ID. Clear Chat button clears the UI without affecting server-side memory.

### 10.3 How It Is Served

The playground is a single string constant (the HTML file) embedded in the framework package. At startup, the framework injects route metadata as a JSON literal into the HTML:

```python
html = PLAYGROUND_HTML.replace("__ROUTES__", routes_json)
return HTMLResponse(html)
```

No filesystem access at runtime. No external HTTP requests. Works offline.

### 10.4 Production Behaviour

The playground is disabled when `YOMAI_ENV=production`. `GET /__yomai__` returns 404. No HTML is served. No performance overhead.

### 10.5 SSE Protocol in the Playground

The playground JavaScript connects to agent endpoints using `fetch` with a streaming reader — not the `EventSource` API. This is because `EventSource` only supports GET requests; agents accept POST.

```javascript
const response = await fetch(route, { method: "POST", body, headers })
const reader   = response.body.getReader()

while (true) {
  const { done, value } = await reader.read()
  if (done) break
  parseSSEChunk(decode(value))
}
```

---

## 11. Frontend Integration

### 11.1 Philosophy

Yomai does not dictate frontend technology. It publishes a stable SSE event schema and ships thin client libraries for the most common environments.

### 11.2 Packages

| Package | Description |
|---|---|
| `@yomai/client` | Vanilla JS client. No framework dependency. Works in any browser. |
| `@yomai/react` | `useAgent()` React hook wrapping the vanilla client. |
| `@yomai/vue` | `useAgent()` Vue composable wrapping the vanilla client. |

All three are thin wrappers. The vanilla client contains all logic. Framework adapters are convenience layers only.

### 11.3 Vanilla Client API

```javascript
import { YomaiClient } from "@yomai/client"

const agent = new YomaiClient("/chat", {
  sessionId : "auto",          // manages localStorage automatically
  onChunk   : (text) => {},   // streaming text delta
  onTool    : (call) => {},   // tool_start event
  onResult  : (res)  => {},   // result event (workflow return value)
  onDone    : (full) => {},   // done event
  onError   : (err)  => {},   // error event
})

agent.send("What's the weather in Tokyo?")
agent.abort()                  // cancel mid-stream

// Convenience: collect stream and return final text
const text = await agent.call("What's the weather in Tokyo?")
```

**Async workflow handling:** When `.send()` receives a `202 Accepted` response, the client automatically fetches the `stream_url` and continues streaming from there. No code change required in the application layer.

### 11.4 React Hook

```jsx
import { useAgent } from "@yomai/react"

function Chat() {
  const { messages, send, isStreaming, activeTools } = useAgent("/chat")

  return (
    <div>
      {messages.map(m => <Message key={m.id} {...m} />)}
      {activeTools.map(t => <ToolBadge key={t.name} tool={t} />)}
      <input onKeyDown={e => e.key === "Enter" && send(e.target.value)} />
    </div>
  )
}
```

---

## 12. File Structure

```
pyproject.toml               # Package metadata, dependencies, CLI entry point
yomai/
├── core/
│   ├── app.py              # Yomai class, @app.agent, @app.workflow, Depends, RouteGroup
│   ├── agent.py            # Agent loop, tool execution
│   └── router.py           # AgentRoute, WorkflowRoute, GetRoute, DeleteRoute,
│                           #   HeadRoute, OptionsRoute, PatchRoute, PutRoute, HTTP/SSE wiring
│
├── workflow/
│   ├── runner.py           # WorkflowRunner, step(), parallel()
│   └── events.py           # Step progress SSE events, result event
│
├── memory/
│   ├── base.py             # MemoryBackend ABC
│   ├── dict.py             # In-memory backend (V1)
│   └── redis.py            # Redis backend (V2)
│
├── tools/
│   ├── decorator.py        # @tool
│   └── registry.py         # Schema generation, tool registry
│
├── llm/
│   ├── base.py             # LLMProvider ABC, LLMEvent types
│   ├── anthropic.py        # Claude adapter
│   └── openai.py           # OpenAI adapter
│
├── streaming/
│   └── sse.py              # SSE formatting, heartbeat, disconnect detection
│
├── middleware/
│   ├── cors.py             # CORSMiddleware
│   ├── errors.py           # Pretty error formatting
│   └── logging.py          # Request logging
│
├── devui/
│   └── playground.py       # Playground HTML (embedded string constant)
│
├── testing/                # V1 — ships with core
│   ├── client.py           # YomaiTestClient
│   ├── mock_llm.py         # mock_llm context manager
│   └── capture_tools.py    # capture_tools context manager
│
├── cli/
│   └── main.py             # yomai new, yomai run
│
├── config.py               # All config dataclasses
├── events.py               # Hook event types
├── exceptions.py           # YomaiError subclasses
└── __init__.py             # Public API exports
```

Every package directory contains an `__init__.py` so the public import paths in section 4.3 are stable.

---

## 13. Versioning & Roadmap

### Release Strategy

Yomai follows semantic versioning. The public API (decorators, config keys, SSE event schema, CLI commands) is stable from 1.0.0. Internal modules (prefixed with `_`) are not part of the public API and may change between minor versions.

### Roadmap Summary

| Version | Theme | Key Features |
|---|---|---|
| **1.0** | Working | Agent endpoints, SSE streaming, tool calling, in-memory sessions, workflows, testing utilities, playground, CLI |
| **1.1** | Polish | Better errors, additional adapters, observability polish |
| **2.0** | Production | Redis memory, parallel tools, async workflows, checkpointing, hooks, rate limiting, budget caps, metrics |
| **2.1** | Observability | OpenTelemetry, LangSmith/LangFuse integration, structured logs |
| **3.0** | Scale | Multi-agent coordination, WebSocket, long-running jobs, image input |
| **3.x** | Platform | `yomai deploy`, managed Redis, usage dashboard |

### Backwards Compatibility Promise

- SSE event schema: stable from 1.0. New event types may be added but existing ones will not change.
- Public API: no breaking changes within a major version.
- Config keys: deprecated keys will emit warnings for one full minor version before removal.
- CLI commands: stable from 1.0. New flags may be added. Existing flags will not be removed within a major version.

---

*Yomai — agents should be easy to build, easy to ship, and easy to debug.*