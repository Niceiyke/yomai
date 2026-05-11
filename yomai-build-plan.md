# Yomai Framework — AI Agent Build Plan

> 8-Phase micro plan for building Yomai V1 with a team of AI agents.
> Each phase has concrete deliverables, acceptance criteria, and a ready-to-paste agent prompt.

---

## Overview

| Phase | Name | Week | Key Deliverable |
|---|---|---|---|
| 0 | Packaging & Skeleton | Week 0 | importable package, `pyproject.toml`, empty module tree |
| 1 | Foundation & SSE Streaming | Week 1 | `@app.agent` with live SSE, no tools |
| 2 | Tool System | Week 2 | `@tool` decorator + agent loop with tool calls |
| 3 | Memory & Sessions | Week 3 | `DictMemory`, session management, auto-IDs |
| 4 | Workflows | Week 3–4 | `@app.workflow`, `WorkflowRunner`, result events |
| 5 | Testing Utilities | Week 4 | `YomaiTestClient`, `mock_llm`, `capture_tools` |
| 6 | CLI, Playground & DevUX | Week 4–5 | `yomai new/run`, playground UI, usage logging |
| 7 | Hardening & OpenAI Adapter | Week 5–6 | OpenAI provider, heartbeat, disconnect, `.call()`, errors |

---

## Repository Structure

Before starting, create this file structure. Every phase builds into it:

```
pyproject.toml
yomai/
├── core/
│   ├── app.py
│   ├── agent.py
│   └── router.py
├── workflow/
│   ├── runner.py
│   └── events.py
├── memory/
│   ├── base.py
│   └── dict.py
├── tools/
│   ├── decorator.py
│   └── registry.py
├── llm/
│   ├── base.py
│   ├── anthropic.py
│   └── openai.py
├── streaming/
│   └── sse.py
├── middleware/
│   ├── cors.py
│   ├── errors.py
│   └── logging.py
├── devui/
│   └── playground.py
├── testing/
│   ├── client.py
│   ├── mock_llm.py
│   └── capture_tools.py
├── cli/
│   └── main.py
├── config.py
├── events.py
├── exceptions.py
└── __init__.py
```

Also create `__init__.py` files in every package directory (`core`, `workflow`, `memory`, `tools`, `llm`, `streaming`, `middleware`, `devui`, `testing`, `cli`) so all public paths are importable.

---

## Phase 0 — Packaging & Skeleton

### Goal
Create an installable, importable Python package skeleton before feature work begins.

### Deliverables
- `pyproject.toml` with package metadata, dependencies, and a `yomai` console script pointing to `yomai.cli.main:app`
- Empty package directories matching the repository structure above, each with `__init__.py`
- Dependency declarations for Starlette, Uvicorn, Pydantic v2, Anthropic, OpenAI, Typer, and httpx

### Acceptance Criteria
```bash
pip install -e .
python -c "import yomai; print(yomai.__version__)"
yomai --help
```

### Agent Prompt — Phase 0

```
Create the package skeleton and packaging metadata for Yomai. Do not implement framework behavior yet. The package must be installable with `pip install -e .`, importable as `yomai`, and expose a `yomai` CLI entry point. Create all directories and `__init__.py` files required by the file structure.
```

## Phase 1 — Foundation & SSE Streaming

### Goal
A working `@app.agent` decorator that accepts a POST request and streams a real LLM response as SSE events. No tools. No memory. Just the wire.

### Deliverables
- `yomai/config.py` — `LLMConfig`, `StreamingConfig`, `DevConfig`, `AgentConfig`, `MemoryConfig` dataclasses with defaults
- `yomai/exceptions.py` — `YomaiError`, `YomaiConfigError`, `YomaiRouteError`, `YomaiLLMError` base classes
- `yomai/streaming/sse.py` — SSE event formatter; produces correctly formatted `event: X\ndata: {...}\n\n` strings for all event types in the spec
- `yomai/llm/base.py` — `LLMProvider` ABC; `LLMEvent` union type (`TextChunk`, `ToolCall`, `Done`)
- `yomai/llm/anthropic.py` — `AnthropicProvider` that streams real Claude responses via the Anthropic SDK; emits `LLMEvent` objects
- `yomai/core/app.py` — `Yomai` class with `@app.agent(path, tools=None)` decorator; mounts Starlette routes
- `yomai/core/router.py` — `AgentRoute` class; wires HTTP POST → StreamingResponse; injects `message` from body; returns SSE stream
- `yomai/__init__.py` — exports `Yomai` only in Phase 1. `tool` is exported in Phase 2 after it is implemented.
- System routes: `GET /__yomai__/health` (returns `{"status": "ok"}`), `GET /__yomai__/routes` (returns list of registered routes)

### Acceptance Criteria
```bash
# This must work end-to-end
pip install yomai
python -c "
from yomai import Yomai
from yomai.config import LLMConfig
import uvicorn

app = Yomai(llm=LLMConfig(provider='anthropic'))

@app.agent('/chat')
async def chat(message: str, session_id: str):
    pass  # marker-only handler; framework handles the LLM call

uvicorn.run(app, port=8000)
"
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Say hello"}' \
  --no-buffer
# Expected: stream of SSE chunk events, then usage event, then done event
```

### SSE Events Required in Phase 1
```
event: chunk
data: {"type": "chunk", "content": "Hello!"}

event: usage
data: {"type": "usage", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0001}

event: done
data: {"type": "done"}

event: error
data: {"type": "error", "message": "...", "code": "..."}
```

---

### Agent Prompt — Phase 1

```
You are a senior Python engineer. Your job is to implement Phase 1 of the Yomai framework: the foundation layer that wires an @app.agent decorator to a live SSE streaming HTTP endpoint.

## What Yomai Is
A FastAPI-style Python framework for serving LLM agents over HTTP. Streaming is the default. Every agent response is a Server-Sent Events (SSE) stream.

## Your Deliverables for Phase 1

Build the following files. Implement them completely — no stubs, no TODOs in critical paths.

### 1. yomai/config.py
Define these dataclasses using Python dataclasses or Pydantic BaseModel (use Pydantic):

```python
class LLMConfig(BaseModel):
    provider: str = "anthropic"           # "anthropic" | "openai"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = Field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    max_tokens: int = 1024
    cost_per_token: dict = Field(default_factory=lambda: {"input": 0.000003, "output": 0.000015})

class MemoryConfig(BaseModel):
    backend: str = "dict"
    ttl_hours: int = 24
    max_messages: int = 20

class AgentConfig(BaseModel):
    max_tool_calls: int = 10
    timeout_secs: int = 120

class StreamingConfig(BaseModel):
    heartbeat_secs: int = 15
    max_duration_secs: int = 300

class DevConfig(BaseModel):
    ui: bool = True
    log_usage: bool = True
    reload: bool = True

class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    dev: DevConfig = Field(default_factory=DevConfig)
```

### 2. yomai/exceptions.py
Define:
- `YomaiError(Exception)` — base class; formats message + hint + docs link
- `YomaiConfigError(YomaiError)`
- `YomaiRouteError(YomaiError)`
- `YomaiLLMError(YomaiError)`
- `YomaiToolError(YomaiError)`
- `YomaiMemoryError(YomaiError)`

Each error should print like:
```
YomaiConfigError: Missing required config: api_key

  LLMConfig.api_key is not set.
  Set the ANTHROPIC_API_KEY environment variable or pass api_key= to LLMConfig.

  Docs: https://yomai.dev/config#api-key
```

### 3. yomai/streaming/sse.py
Implement SSE formatting. The SSE spec requires:
```
event: <name>\ndata: <json>\n\n
```

Implement:
```python
def format_sse(event_type: str, data: dict) -> str:
    """Returns a correctly formatted SSE string."""

async def sse_chunk(content: str) -> str:
async def sse_tool_start(name: str, args: dict, id: str) -> str:
async def sse_tool_end(id: str, result: str, duration_ms: int) -> str:
async def sse_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
async def sse_done() -> str:
async def sse_error(message: str, code: str) -> str:
async def sse_ping() -> str:
```

### 4. yomai/llm/base.py
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Union

@dataclass
class TextChunk:
    content: str

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict

@dataclass
class Done:
    input_tokens: int = 0
    output_tokens: int = 0

LLMEvent = Union[TextChunk, ToolCall, Done]

class LLMProvider(ABC):
    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> AsyncIterator[LLMEvent]: ...
```

### 5. yomai/llm/anthropic.py
Implement `AnthropicProvider(LLMProvider)` using the `anthropic` Python SDK.
- Use `client.messages.stream()` for streaming
- Map Anthropic SDK events to `LLMEvent` types:
  - `content_block_delta` with type `text_delta` → `TextChunk`
  - `content_block_stop` with type `tool_use` → `ToolCall`
  - `message_stop` → `Done` with token counts from `message.usage`
- Handle `anthropic.RateLimitError`, `anthropic.AuthenticationError` → raise `YomaiLLMError`
- Accept `tools: list[dict]` in JSON schema format (will be empty list in Phase 1)

### 6. yomai/core/app.py
Implement the `Yomai` class:

```python
class Yomai:
    def __init__(self, llm: LLMConfig = None, memory=None, agent=None, streaming=None, dev=None):
        # store config, build Starlette app, mount system routes
    
    def agent(self, path: str, tools: list | None = None):
        # decorator factory — registers AgentRoute for this path
        # raises YomaiRouteError if path already registered
    
    def add_middleware(self, middleware_class, **kwargs):
        # delegate to Starlette
    
    # Make Yomai itself an ASGI app
    async def __call__(self, scope, receive, send): ...
```

System routes to mount at init:
- `GET /__yomai__/health` → `{"status": "ok", "version": "1.0.0"}`
- `GET /__yomai__/routes` → list of `{"path": "/chat", "type": "agent", "tools": [...]}`

### 7. yomai/core/router.py
Implement `AgentRoute`:
- Handles `POST <path>`
- Extracts `message: str` from JSON body (400 if missing)
- Reads `X-Session-Id` header; auto-generates `uuid.uuid4()` if absent
- Sets response headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`, `X-Session-Id: <id>`
- Calls `AnthropicProvider.stream(messages=[{"role":"user","content":message}], tools=[], system="")`
- For each `LLMEvent`:
  - `TextChunk` → yield `sse_chunk(content)`
  - `Done` → yield `sse_usage(...)` then `sse_done()`
- Catches ALL exceptions, yields `sse_error(...)`, then closes stream — server never crashes on per-request errors

### 8. yomai/__init__.py
```python
from yomai.core.app import Yomai

__all__ = ["Yomai"]
__version__ = "1.0.0"
```

Do not import or export `tool` until Phase 2, because `yomai.tools.decorator` is not implemented in Phase 1.

## Technical Constraints
- Use Starlette (not FastAPI) as the ASGI base
- Use the `anthropic` Python SDK for Claude
- Use Pydantic v2 for config validation
- All agent handler functions and provider methods must be async
- The SSE stream must be a true streaming response — do not buffer the full LLM response before sending
- Use `StreamingResponse` from Starlette with `media_type="text/event-stream"`

## What NOT to Build Yet
Do not implement: @tool decorator, tool execution, memory/sessions, workflows, playground, CLI, testing utilities. Those are Phase 2–6.

## Deliverable Format
Return the complete, working implementation of each file listed above. Include all imports. Each file should be self-contained and importable.
```

---

## Phase 2 — Tool System

### Goal
The `@tool` decorator and the agent loop. The LLM can now call Python functions. Tool calls are intercepted, executed, and fed back to the LLM transparently.

### Deliverables
- `yomai/tools/decorator.py` — `@tool` decorator; extracts JSON schema from type annotations via Pydantic; supports sync and async functions; registers in global registry
- `yomai/tools/registry.py` — `ToolRegistry`; stores registered tools; generates Anthropic/OpenAI-compatible tool schema lists
- `yomai/core/agent.py` — `AgentLoop`; the agentic loop: stream → detect tool call → execute → feed back → continue; enforces `max_tool_calls`; emits `tool_start` and `tool_end` SSE events
- Update `yomai/core/router.py` — pass tools from `@app.agent(tools=[...])` into the agent loop

### Acceptance Criteria
```python
from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))

@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"

@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str):
    pass

# When asked "What's the weather in Tokyo?", the SSE stream must contain:
# event: tool_start {"name": "get_weather", "args": {"city": "Tokyo"}, "id": "..."}
# event: tool_end   {"id": "...", "result": "72°F and sunny in Tokyo", "duration_ms": N}
# event: chunk      {"content": "It's 72°F and sunny in Tokyo!"}
# event: done
```

### SSE Events Added in Phase 2
```
event: tool_start
data: {"type": "tool_start", "name": "get_weather", "args": {"city": "Tokyo"}, "id": "t1"}

event: tool_end
data: {"type": "tool_end", "id": "t1", "result": "72°F and sunny", "duration_ms": 142}
```

---

### Agent Prompt — Phase 2

```
You are a senior Python engineer continuing work on the Yomai framework. Phase 1 is complete: @app.agent streams real LLM responses over SSE. Now build Phase 2: the tool system.

## Context (already built in Phase 1)
- `yomai/llm/base.py` — `LLMProvider` ABC, `TextChunk`, `ToolCall`, `Done` event types
- `yomai/llm/anthropic.py` — `AnthropicProvider.stream()` yields `LLMEvent` objects
- `yomai/streaming/sse.py` — `sse_chunk`, `sse_done`, `sse_error`, `sse_usage` formatters
- `yomai/core/router.py` — `AgentRoute` handles POST → SSE stream

## Your Deliverables for Phase 2

### 1. yomai/tools/decorator.py
Implement the `@tool` decorator and update `yomai/__init__.py` to export `tool` (`__all__ = ["Yomai", "tool"]`):

```python
@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"
```

Requirements:
- Works on both `async def` and `def` functions
- Extracts JSON schema from type annotations using Pydantic's `TypeAdapter` or `model_json_schema`
- Each parameter becomes a JSON Schema property with its Python type mapped to JSON Schema type
- The function's docstring becomes the tool's `description`
- Optional parameters (with defaults) are not added to the `required` list
- After decoration, the function is still callable normally (`await get_weather("Tokyo")` works)
- Attaches a `.schema` attribute to the function: the JSON Schema dict
- Attaches a `.tool_name` attribute: the function name as a string
- Registers the function in the global `ToolRegistry`

Supported type mappings:
- `str` → `{"type": "string"}`
- `int` → `{"type": "integer"}`
- `float` → `{"type": "number"}`
- `bool` → `{"type": "boolean"}`
- `list` → `{"type": "array"}`
- `dict` → `{"type": "object"}`

### 2. yomai/tools/registry.py
Implement `ToolRegistry`:

```python
class ToolRegistry:
    def register(self, fn: Callable) -> None: ...
    def get(self, name: str) -> Callable | None: ...
    def get_schemas_for_anthropic(self, tools: list[Callable]) -> list[dict]: ...
    def get_schemas_for_openai(self, tools: list[Callable]) -> list[dict]: ...

# Global singleton
_registry = ToolRegistry()
```

Anthropic tool schema format:
```json
{
  "name": "get_weather",
  "description": "Get current weather for a city.",
  "input_schema": {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"]
  }
}
```

OpenAI tool schema format (for Phase 7):
```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "description": "Get current weather for a city.",
    "parameters": {
      "type": "object",
      "properties": {"city": {"type": "string"}},
      "required": ["city"]
    }
  }
}
```

### 3. yomai/core/agent.py
Implement `AgentLoop` — the heart of Yomai:

```python
class AgentLoop:
    def __init__(self, provider: LLMProvider, tools: list[Callable], config: AgentConfig):
        ...

    async def run(
        self,
        message: str,
        history: list[dict],   # conversation history from memory
        system: str = "",
    ) -> AsyncIterator[str]:   # yields raw SSE strings
        ...
```

The loop algorithm:
```
messages = history + [{"role": "user", "content": message}]
tool_schemas = self.provider.tool_schemas(self.tools) if hasattr(self.provider, "tool_schemas") else registry.get_schemas_for_anthropic(self.tools)
iterations = 0

while iterations < self.config.max_tool_calls:
    async for event in self.provider.stream(messages, tool_schemas, system):
        if isinstance(event, TextChunk):
            yield sse_chunk(event.content)
            accumulated_text += event.content
        elif isinstance(event, ToolCall):
            pending_tool_calls.append(event)
        elif isinstance(event, Done):
            usage = event
    
    if not pending_tool_calls:
        break
    
    # Execute all pending tool calls (sequential in V1)
    for tool_call in pending_tool_calls:
        yield sse_tool_start(tool_call.name, tool_call.args, tool_call.id)
        start = time.monotonic()
        try:
            fn = registry.get(tool_call.name)
            if inspect.iscoroutinefunction(fn):
                result = await fn(**tool_call.args)
            else:
                result = await asyncio.to_thread(functools.partial(fn, **tool_call.args))
        except Exception as e:
            result = f"Error: {str(e)}"
        duration_ms = int((time.monotonic() - start) * 1000)
        yield sse_tool_end(tool_call.id, str(result), duration_ms)
        
        # Append assistant message and tool result to messages
        messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": tool_call.id, "name": tool_call.name, "input": tool_call.args}
        ]})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_call.id, "content": str(result)}
        ]})
    
    pending_tool_calls = []
    iterations += 1

if iterations >= self.config.max_tool_calls:
    yield sse_error("Maximum tool calls reached", "max_tool_calls_exceeded")

yield sse_usage(usage.input_tokens, usage.output_tokens, estimated_cost)
yield sse_done()
```

Important implementation notes:
- If a tool function is a regular `def` (not async), run it with `await asyncio.to_thread(functools.partial(fn, **args))` so it does not block the event loop
- Accumulated `accumulated_text` is the full text response (used by memory in Phase 3)
- The loop must handle the case where the LLM calls a tool that doesn't exist in the registry: yield `sse_error` for that specific tool call but continue the stream
- `AgentLoop.run()` is an async generator and must not attempt to `return` a final value. It must accumulate text on `self.last_reply` and usage on `self.last_usage`; callers read those attributes after the generator exhausts.

### 4. Update yomai/core/router.py
- Pass `tools` list from `@app.agent(path, tools=[...])` into `AgentLoop`
- Instantiate `AgentLoop(provider, tools, agent_config)` per request (stateless)
- Replace the direct `provider.stream()` call with `agent_loop.run(message, history=[], system="")`
- History is an empty list for now (Phase 3 adds memory)

## Technical Constraints
- Sequential tool execution only (no asyncio.gather on tools — that's V2)
- Tool schemas must exactly match Anthropic's expected format or the API will reject them
- Test your Anthropic tool schema generation against the Anthropic API before considering this done
- The `AgentLoop` must be stateless across requests — all state lives in the `messages` list built per-request

## What NOT to Build Yet
Memory, sessions, workflows, CLI, playground, testing utilities.
```

---

## Phase 3 — Memory & Sessions

### Goal
Per-session conversation history. Every request loads history before the LLM runs and saves the exchange after it completes. Sessions are identified by `X-Session-Id`.

### Deliverables
- `yomai/memory/base.py` — `MemoryBackend` ABC (published as public API)
- `yomai/memory/dict.py` — `DictMemory` in-process backend; 20-message cap per session; `load`, `save`, `clear` methods
- Update `yomai/core/router.py` — load history before `AgentLoop.run()`; save after; auto-generate session UUID if header absent; return `X-Session-Id` in response header

### Acceptance Criteria
```python
# First request
curl -X POST http://localhost:8000/chat \
  -H "X-Session-Id: test-session-1" \
  -d '{"message": "My name is Sarah"}'

# Second request — agent must remember the name
curl -X POST http://localhost:8000/chat \
  -H "X-Session-Id: test-session-1" \
  -d '{"message": "What is my name?"}'
# Response chunks must contain "Sarah"

# Auto-generated session ID
curl -X POST http://localhost:8000/chat \
  -d '{"message": "Hello"}'
# Response headers must include: X-Session-Id: <uuid>
```

---

### Agent Prompt — Phase 3

```
You are a senior Python engineer continuing work on the Yomai framework. Phases 1 and 2 are complete: agents stream LLM responses and can call tools. Now add memory: per-session conversation history.

## Context (already built)
- `yomai/core/agent.py` — `AgentLoop.run(message, history, system)` accepts history as a list of dicts
- `yomai/core/router.py` — `AgentRoute` handles POST → SSE; currently passes empty `history=[]`
- `AgentLoop` exposes the final assistant text reply as `agent_loop.last_reply` after `run()` exhausts (needed for memory saving)

## Your Deliverables for Phase 3

### 1. yomai/memory/base.py
```python
from abc import ABC, abstractmethod

class MemoryBackend(ABC):
    @abstractmethod
    async def load(self, session_id: str) -> list[dict]:
        """Return conversation history as a list of OpenAI-style message dicts."""
    
    @abstractmethod
    async def save(self, session_id: str, user_message: str, assistant_reply: str) -> None:
        """Append the user message and assistant reply to history."""
    
    @abstractmethod
    async def clear(self, session_id: str) -> None:
        """Delete all history for this session."""
```

This ABC is part of the public API. Developers implement it to create custom backends.

### 2. yomai/memory/dict.py
Implement `DictMemory(MemoryBackend)`:

```python
class DictMemory(MemoryBackend):
    def __init__(self, max_messages: int = 20):
        self._store: dict[str, list[dict]] = {}
        self._max = max_messages
```

- `_store` maps `session_id` → list of message dicts
- Message format: `{"role": "user"|"assistant", "content": "..."}`
- `load(session_id)` → returns `[]` for unknown sessions (never raises)
- `save(session_id, user_message, assistant_reply)`:
  - Appends `{"role": "user", "content": user_message}`
  - Appends `{"role": "assistant", "content": assistant_reply}`
  - If total messages exceed `max_messages`, drop the oldest messages (keep the most recent `max_messages`)
  - Never drops the first message if it has role "system"
- `clear(session_id)` → deletes the session key (no-op if not found)
- Thread-safe: use `asyncio.Lock` per session or a global lock

### 3. Update yomai/core/router.py
Add these steps to the request handler:

**Before AgentLoop.run():**
```python
# 1. Read or generate session ID
session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())

# 2. Load history
memory: MemoryBackend = self.memory_backend  # injected from app
history = await memory.load(session_id)
```

**Pass to AgentLoop:**
```python
# Run the loop with history
assistant_reply = ""
async for sse_string in agent_loop.run(message, history=history, system=""):
    yield sse_string
    # after the generator exhausts, read agent_loop.last_reply for memory saving
```

**After AgentLoop.run() completes:**
```python
# 3. Save the exchange
assistant_reply = agent_loop.last_reply
await memory.save(session_id, message, assistant_reply)
```

**Response headers must include:**
```python
headers = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
    "X-Session-Id": session_id,   # always set, even if auto-generated
}
```

**Challenge:** The `AgentLoop.run()` method is an async generator. You need to capture the full assistant reply text while also streaming SSE events. Design pattern options:
- Have `AgentLoop` accumulate text internally and expose it via a `.last_reply` attribute after the generator exhausts
- Use a wrapper class that captures text while yielding SSE strings
- Use an async queue (producer/consumer)

Pick the cleanest approach. The memory save MUST happen after the full stream completes (not before `done` is sent).

### 4. Update yomai/core/app.py
- Instantiate `DictMemory(max_messages=config.memory.max_messages)` at startup
- Inject it into `AgentRoute` instances

## Technical Constraints
- `DictMemory` is in-process and not persisted across restarts — document this clearly in the docstring
- The `max_messages` cap applies after saving, not before — keep the last N messages
- If `save()` is called with an empty `assistant_reply` (e.g. LLM errored mid-stream), still save the user message so the session isn't corrupted — or save nothing. Your call, but be consistent.
- Session IDs are not authenticated — do not validate them. Any string is accepted as a session ID.

## What NOT to Build Yet
Redis backend, TTL eviction, memory summarisation — those are V2.
```

---

## Phase 4 — Workflows

### Goal
`@app.workflow` and `WorkflowRunner`. Developers define multi-step agent pipelines in Python. The framework sequences the steps and streams progress events to the client.

### Deliverables
- `yomai/workflow/events.py` — `step_start`, `step_done`, `result` SSE event formatters
- `yomai/workflow/runner.py` — `WorkflowRunner`; implements `step()` and `parallel()`; emits step events; returns step output
- Update `yomai/core/app.py` — `@app.workflow(path)` decorator; registers `WorkflowRoute`
- `yomai/core/router.py` — `WorkflowRoute`; wires `POST <path>` → SSE stream → workflow execution

### Acceptance Criteria
```python
@app.workflow("/research")
async def research_workflow(topic: str, runner: WorkflowRunner):
    results  = await runner.step("search",  searcher_agent, topic)
    analysis = await runner.step("analyze", analyst_agent,  results)
    report   = await runner.step("write",   writer_agent,   analysis)
    return report

# SSE stream must contain:
# event: step_start {"type":"step_start","name":"search","index":1,"of":null}
# (streaming chunks from searcher_agent)
# event: step_done  {"type":"step_done","name":"search","duration_ms":1200}
# event: step_start {"type":"step_start","name":"analyze","index":2,"of":null}
# ...
# event: result     {"type":"result","content":"<final report>"}
# event: done
```

### SSE Events Added in Phase 4
```
event: step_start
data: {"type": "step_start", "name": "search", "index": 1, "of": null}

event: step_done
data: {"type": "step_done", "name": "search", "duration_ms": 1200}

event: result
data: {"type": "result", "content": "..."}
```

---

### Agent Prompt — Phase 4

```
You are a senior Python engineer continuing work on the Yomai framework. Phases 1–3 are complete. Now build the workflow system: @app.workflow and WorkflowRunner.

## Context (already built)
- `AgentLoop.run(message, history, system)` streams SSE and stores the assistant reply on `agent_loop.last_reply`
- `DictMemory` handles per-session history
- SSE formatters in `yomai/streaming/sse.py`

## Core Concept
A workflow is a Python async function decorated with `@app.workflow`. It receives the user's input and a `WorkflowRunner` instance. It calls agents as steps using `runner.step()`. The return value of the function is emitted as a `result` SSE event.

Conditional branching and loops are plain Python — no special framework primitives needed.

## Your Deliverables for Phase 4

### 1. yomai/workflow/events.py
Add these formatters to match the SSE schema:
```python
def sse_step_start(name: str, index: int, of: int | None = None) -> str: ...
def sse_step_done(name: str, duration_ms: int) -> str: ...
def sse_result(content: str) -> str: ...
```

### 2. yomai/workflow/runner.py

```python
class WorkflowRunner:
    def __init__(self, sse_queue: asyncio.Queue, session_id: str, memory: MemoryBackend):
        # sse_queue: all SSE strings yielded by steps go here for the HTTP layer to stream
        # session_id: workflows use a shared session
        # memory: for passing to AgentLoop instances
    
    async def step(self, name: str, agent_fn: Callable, input: Any) -> Any:
        """
        Run one workflow step.
        1. Emit step_start event
        2. Call agent_fn with input (agent_fn is a decorated @app.agent function)
        3. Stream all SSE events from the agent to sse_queue
        4. Emit step_done event
        5. Return the agent's final text output
        """
    
    async def parallel(self, steps: list[Coroutine]) -> list[Any]:
        """
        Run multiple workflow steps concurrently using asyncio.gather.
        Returns list of results in the same order as steps.
        """
```

**Key design challenge:** `runner.step()` needs to call an `@app.agent`-decorated function and get both its SSE stream AND its final text output. Design options:
- `@app.agent` functions expose an internal `._run(message, history)` method that `runner.step` can call directly, bypassing HTTP
- `WorkflowRunner` calls `AgentLoop` directly, not the HTTP route

The second approach is cleaner. `runner.step()` should instantiate an `AgentLoop` with the appropriate provider and tools, call `.run()`, stream SSE events to the queue, and return the text output. The `agent_fn` parameter provides the tools and config needed.

To make this work, `@app.agent`-decorated functions must expose:
- `._yomai_tools` — list of tool functions registered for this agent
- `._yomai_agent_config` — AgentConfig for this agent
- `._yomai_app` — parent `Yomai` app, used by `WorkflowRunner` to access the provider, memory, and config

### 3. Update yomai/core/app.py

Add `@app.workflow(path)` decorator:
```python
def workflow(self, path: str):
    def decorator(fn: Callable):
        # register WorkflowRoute for this path
        fn._is_workflow = True
        return fn
    return decorator
```

`GET /__yomai__/routes` must now include workflow routes with `"type": "workflow"`.

### 4. yomai/core/router.py — WorkflowRoute

```python
class WorkflowRoute:
    async def handle(self, request: Request) -> StreamingResponse:
        # 1. Parse input from request body
        # 2. Generate/read session_id
        # 3. Create asyncio.Queue for SSE events
        # 4. Create WorkflowRunner(queue, session_id, memory)
        # 5. Run the workflow function in a task:
        #    result = await workflow_fn(input, runner=runner)
        #    await queue.put(sse_result(result))
        #    await queue.put(sse_done())
        #    await queue.put(None)  # sentinel: stream done
        # 6. Stream from queue until sentinel
```

**Workflow input:** Unlike agents (which always receive a `message: str`), workflows declare their own parameters in the function signature. The framework inspects the function signature and extracts matching fields from the request body JSON. Use Python's `inspect.signature()` for this.

Example:
```python
@app.workflow("/research")
async def research_workflow(topic: str, runner: WorkflowRunner):
    ...
```
Request body: `{"topic": "quantum computing"}` → `topic = "quantum computing"`, `runner = WorkflowRunner(...)` injected by framework.

The `runner` parameter is always injected by the framework and must never be read from the request body.

## Technical Constraints
- `runner.parallel()` uses `asyncio.gather` — this runs multiple WORKFLOW STEPS concurrently. This is NOT the same as parallel LLM tool calls (that's V2).
- Workflows do not have their own session — each `runner.step()` call that involves an agent uses the workflow's session_id
- If a workflow step raises an exception, emit `sse_error`, emit `sse_done`, close the stream. Do not crash the server.
- The `result` SSE event is emitted only for workflows. Agents do NOT emit `result`.
- Workflow return values: support `str`, `dict`, `list`. Serialize dict/list to JSON string for the `result` event `content` field.
```

---

## Phase 5 — Testing Utilities

### Goal
Deterministic testing of agent behavior without hitting the real LLM API. Three utilities: a test client, an LLM mock, and a tool call capture context manager.

### Deliverables
- `yomai/testing/client.py` — `YomaiTestClient`; wraps ASGI app; `.stream()` and `.call()` methods
- `yomai/testing/mock_llm.py` — `mock_llm` context manager; intercepts `LLMProvider.stream()` calls with scripted responses
- `yomai/testing/capture_tools.py` — `capture_tools` context manager; records tool calls without executing them

### Acceptance Criteria
```python
async def test_weather_agent():
    with mock_llm(responses=["It's 72°F in Tokyo"]):
        client = YomaiTestClient(app)
        chunks = await client.stream("/chat", message="Weather in Tokyo?")
        assert "72°F" in "".join(chunks)

async def test_tool_was_called():
    client = YomaiTestClient(app)
    with capture_tools() as calls:
        await client.stream("/chat", message="Weather in Tokyo?")
    assert calls[0].name == "get_weather"
    assert calls[0].args == {"city": "Tokyo"}

async def test_memory_persists():
    client = YomaiTestClient(app)
    sid = "test-session-1"
    await client.stream("/chat", message="My name is Sarah", session_id=sid)
    reply = await client.call("/chat", message="What's my name?", session_id=sid)
    assert "Sarah" in reply
```

---

### Agent Prompt — Phase 5

```
You are a senior Python engineer continuing work on the Yomai framework. Phases 1–4 are complete. Now build the testing utilities: YomaiTestClient, mock_llm, and capture_tools.

## Why This Matters
LLM outputs are non-deterministic. Testing tool calls, memory behavior, and workflow sequencing requires bypassing the LLM entirely. The testing utilities provide deterministic control at the LLM layer.

## Your Deliverables for Phase 5

### 1. yomai/testing/client.py

```python
class YomaiTestClient:
    def __init__(self, app: Yomai):
        # Use Starlette's TestClient or httpx.AsyncClient with ASGITransport
        # Prefer async (httpx) since agents are async
    
    async def stream(
        self,
        path: str,
        message: str,
        session_id: str | None = None,
        extra_body: dict = {},
    ) -> list[str]:
        """
        POST to path, collect all SSE chunk events, return list of chunk content strings.
        Does NOT return raw SSE — returns just the text content from chunk events.
        """
    
    async def call(
        self,
        path: str,
        message: str,
        session_id: str | None = None,
    ) -> str:
        """
        Convenience: stream and join all chunks into a single string.
        """
    
    async def get_events(
        self,
        path: str,
        message: str,
        session_id: str | None = None,
    ) -> list[dict]:
        """
        Return ALL SSE events as parsed dicts, not just chunk content.
        Useful for asserting on tool_start, tool_end, usage, done events.
        """
```

Use `httpx.AsyncClient` with `transport=httpx.ASGITransport(app=app)` for in-process ASGI testing.

Parse SSE responses by splitting on `\n\n`, then parsing `event:` and `data:` lines.

### 2. yomai/testing/mock_llm.py

```python
from contextlib import contextmanager
from dataclasses import dataclass

@dataclass
class MockToolCall:
    name: str
    args: dict
    id: str = "mock-tool-1"

@contextmanager
def mock_llm(
    responses: list[str | MockToolCall | list[str | MockToolCall]] = [],
):
    """
    Context manager that replaces LLMProvider.stream() with a mock.
    
    `responses` is a list of LLM turns. Each provider.stream() call consumes one turn. For convenience, a bare str or MockToolCall is treated as a single-item turn.
    - str: emitted as TextChunk events
    - MockToolCall: emitted as ToolCall events
    - list[str | MockToolCall]: multiple events in one LLM turn
    
    Example:
        with mock_llm(responses=[[MockToolCall("get_weather", {"city": "Tokyo"})], ["It's 72°F"]]):
            # First call: LLM "decides" to call get_weather
            # Second call: LLM responds with "It's 72°F"
    
    If the mock runs out of turns, subsequent calls return an empty text response.
    """
```

Implementation approach:
- Monkey-patch the `LLMProvider.stream` method on the active provider instance
- OR use a `MockLLMProvider` and temporarily swap it into the app's config
- The mock must yield the correct `LLMEvent` types (`TextChunk`, `ToolCall`, `Done`)
- `Done` is appended automatically at the end of each consumed LLM turn
- The context manager restores the original provider on exit (including on exception)

### 3. yomai/testing/capture_tools.py

```python
from contextlib import contextmanager
from dataclasses import dataclass

@dataclass
class CapturedToolCall:
    name: str
    args: dict
    result: str | None = None
    duration_ms: int = 0

@contextmanager
def capture_tools(return_value: str = "mocked tool result"):
    """
    Context manager that intercepts all tool function calls.
    
    - Records each tool call (name, args)
    - Does NOT execute the real tool function
    - Returns `return_value` to the LLM for all tool calls
    - After the context exits, `calls` contains all intercepted calls
    
    Usage:
        with capture_tools() as calls:
            await client.stream("/chat", message="Weather?")
        assert calls[0].name == "get_weather"
        assert calls[0].args["city"] == "Tokyo"
    """
```

Implementation approach:
- Monkey-patch the `ToolRegistry.get()` method to return a wrapper function
- The wrapper records the call and returns `return_value` without calling the real function
- Or wrap individual tool functions in the registry temporarily
- Restore originals on exit

### 4. yomai/testing/__init__.py
```python
from yomai.testing.client import YomaiTestClient
from yomai.testing.mock_llm import mock_llm, MockToolCall
from yomai.testing.capture_tools import capture_tools, CapturedToolCall

__all__ = ["YomaiTestClient", "mock_llm", "MockToolCall", "capture_tools", "CapturedToolCall"]
```

## Technical Constraints
- Tests must run without any real LLM API key when `mock_llm` is active
- `capture_tools` must work independently of `mock_llm` — they can be combined but are not required to be
- The test client must handle SSE parsing correctly: split on `\n\n`, parse `event:` and `data:` lines, skip `ping` events
- Use `pytest-asyncio` compatible patterns (async test functions)
- Do not use threading or multiprocessing — everything runs in the same asyncio event loop as the app
```

---

## Phase 6 — CLI, Playground & Developer UX

### Goal
`yomai new` and `yomai run` CLI commands. The `/__yomai__` playground UI. Usage logging. Pretty error formatting.

### Deliverables
- `yomai/cli/main.py` — `yomai new <name>` (scaffold project) and `yomai run` (dev server with pretty output)
- `yomai/devui/playground.py` — single-file HTML playground embedded as a string constant; injects route metadata at startup
- `yomai/middleware/errors.py` — pretty error formatting; hides stack traces in production
- `yomai/middleware/logging.py` — request logging middleware with the formatted dev output from the spec
- Update `yomai/core/app.py` — mount playground at `/__yomai__` (dev only); inject middleware

### Acceptance Criteria
```bash
yomai new my-project
# Creates: my-project/main.py, my-project/tools.py, my-project/requirements.txt, my-project/.env.example

cd my-project
yomai run
# Prints: routes list, playground URL, starts uvicorn with --reload

# GET http://localhost:8000/__yomai__
# Returns: HTML playground with agent switcher, chat UI, event log, usage bar, session controls

# YOMAI_ENV=production yomai run
# GET http://localhost:8000/__yomai__ → 404
```

---

### Agent Prompt — Phase 6

```
You are a senior Python engineer continuing work on the Yomai framework. Phases 1–5 are complete. Now build the developer experience layer: CLI, playground, and logging.

## Your Deliverables for Phase 6

### 1. yomai/cli/main.py
Use Typer to implement two commands:

```python
import typer
app = typer.Typer()

@app.command()
def new(project_name: str): ...

@app.command()
def run(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = True,
): ...
```

**`yomai new <project-name>`** scaffolds:
```
<project-name>/
├── main.py           # sample agent with get_weather tool
├── tools.py          # @tool examples
├── requirements.txt  # yomai, uvicorn, anthropic
└── .env.example      # ANTHROPIC_API_KEY=your-key-here
```

The sample `main.py`:
```python
from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))

@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"  # replace with real API call

@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str):
    pass
```

**`yomai run`** should:
1. Import `main:app` using importlib (like uvicorn does)
2. Print the startup banner:
```
  Yomai v1.0.0  ·  http://localhost:8000
  Playground  →  http://localhost:8000/__yomai__

  Routes
    POST  /chat     AgentRoute   tools: [get_weather]
```
3. Start uvicorn with `--reload` if reload=True
4. Read port/host from args or env vars (`PORT`, `HOST`)

### 2. yomai/devui/playground.py

Build a single-file HTML playground. It is embedded as a Python string constant:

```python
PLAYGROUND_HTML = """<!DOCTYPE html>..."""

def get_playground_html(routes: list[dict]) -> str:
    import json
    routes_json = json.dumps(routes)
    return PLAYGROUND_HTML.replace("__ROUTES__", routes_json)
```

The playground HTML must implement:

**Layout:**
- Left sidebar: agent/workflow switcher dropdown (populated from `__ROUTES__` JSON), session ID display, "New Session" button, "Clear Chat" button
- Main area: chat message list (messages stream word by word), input box at bottom
- Right panel (collapsible): tool call panel (name, args, result, duration for each call), raw SSE event log (toggle friendly/raw JSON)
- Footer: usage bar (tokens in/out, cost estimate)

**JavaScript requirements:**
- Use `fetch` with streaming reader (NOT `EventSource` — agents are POST, not GET)
- Parse SSE chunks: split on `\n\n`, extract `event:` and `data:` fields
- Handle each event type: `chunk` (append to current message), `tool_start` (show tool badge), `tool_end` (update badge with result), `usage` (update footer), `done` (finalize), `error` (show error state), `ping` (ignore)
- Session ID stored in a JS variable (not localStorage per spec — the playground is a debugging tool)
- "New Session" generates a new UUID
- Agent switcher starts a new session when switched

**Styling:**
- Dark theme
- Monospace font for event log
- Tool calls shown as collapsible cards
- Streaming text animates character by character
- No external CSS frameworks — inline styles only

No external dependencies. The playground must work offline.

### 3. yomai/middleware/logging.py

Implement `LoggingMiddleware` using Starlette's middleware interface.

Log format for agent requests:
```
[12:01:33] POST /chat  session=abc123
           ⚙ get_weather(city="Tokyo")  →  "72°F sunny"  142ms
           ✓ 2.3s  ·  342→89 tokens  ·  ~$0.0004 (est.)
```

Log format for workflow requests:
```
[12:01:40] POST /research  session=def456
           ▸ step 1/3  search    1.2s  ✓
           ▸ step 2/3  analyze   4.5s  ✓
           ▸ step 3/3  write     3.1s  ✓
           ✓ 8.8s  ·  ~$0.012 (est.)
```

Capture timing and event data by parsing/internal-observing the same SSE events emitted by AgentLoop/WorkflowRunner. Do not depend on the public hooks system; hooks are deferred to V2.

Only log when `dev.log_usage = True`.

### 4. yomai/middleware/errors.py

Implement `ErrorMiddleware`:
- In dev mode: show full stack trace in terminal + friendly SSE error to client
- In production (`YOMAI_ENV=production`): log error server-side, send generic SSE error to client (no stack trace leakage)

Pretty format (dev mode):
```
YomaiToolError: Tool 'get_weather' raised an exception

  TypeError: city must be a string, got int
  File "tools.py", line 12, in get_weather

  Hint: Check the type annotations on your tool function.

  Docs: https://yomai.dev/tools#errors
```

### 5. Update yomai/core/app.py

Mount playground conditionally:
```python
import os

if os.environ.get("YOMAI_ENV") != "production" and self.config.dev.ui:
    # Mount GET /__yomai__ → playground HTML
    routes_data = self._get_routes_metadata()
    html = get_playground_html(routes_data)
    # Serve it as an HTMLResponse
else:
    # GET /__yomai__ → 404
```

## Technical Constraints
- Playground HTML must be a single string constant — no file reads at runtime
- The `__ROUTES__` placeholder must be replaced with valid JSON before serving
- `yomai run` must work from any directory that has a `main.py` (like uvicorn)
- Dev playground is completely absent in production — no 404 that reveals the path exists, no HTML served, no overhead
```

---

## Phase 7 — Hardening & OpenAI Adapter

### Goal
Production-grade hardening. OpenAI adapter. Heartbeat. Graceful disconnect handling. `.call()` convenience method. Graceful shutdown. Final polish.

### Deliverables
- `yomai/llm/openai.py` — `OpenAIProvider(LLMProvider)` using OpenAI SDK; same `LLMEvent` interface
- Update `yomai/streaming/sse.py` — heartbeat coroutine; sends `ping` every N seconds on idle streams
- Update `yomai/core/router.py` — client disconnect detection; cancel LLM stream on disconnect; don't save memory on disconnect
- Update `yomai/core/app.py` — graceful shutdown on `SIGTERM`; 30-second drain window
- Update `yomai/__init__.py` — full public API with type stubs

### Acceptance Criteria
```python
# OpenAI works identically to Anthropic
app = Yomai(llm=LLMConfig(provider="openai", model="gpt-4o"))
# Same agent code, same SSE output

# .call() collects stream to string
from yomai.testing import YomaiTestClient
client = YomaiTestClient(app)
result = await client.call("/chat", message="What is 2+2?")
assert "4" in result

# Heartbeat: idle connections get ping events every 15s
# SIGTERM: server drains active streams before exiting
```

---

### Agent Prompt — Phase 7

```
You are a senior Python engineer completing the Yomai framework. Phases 1–6 are complete. Phase 7 is hardening: OpenAI adapter, heartbeat, disconnect handling, graceful shutdown, and final polish.

## Your Deliverables for Phase 7

### 1. yomai/llm/openai.py

Implement `OpenAIProvider(LLMProvider)` using the `openai` Python SDK:

```python
class OpenAIProvider(LLMProvider):
    def __init__(self, config: LLMConfig):
        self.client = openai.AsyncOpenAI(api_key=config.api_key)
        self.model = config.model
    
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> AsyncIterator[LLMEvent]: ...
```

OpenAI-specific mappings:
- System prompt: prepend as `{"role": "system", "content": system}` in messages list
- Streaming: use `client.chat.completions.create(stream=True, ...)`
- Text chunks: `chunk.choices[0].delta.content`
- Tool calls: streamed incrementally — accumulate `chunk.choices[0].delta.tool_calls`; emit `ToolCall` when the call is complete
- Token counts: in `chunk.usage` on the final chunk (may be None for intermediate chunks)
- Tool schema format: OpenAI uses `{"type": "function", "function": {...}}` — use `ToolRegistry.get_schemas_for_openai()`
- Errors: map `openai.RateLimitError`, `openai.AuthenticationError` → `YomaiLLMError`

The `AgentLoop` must work unchanged with either provider — all differences are encapsulated in the provider class.

### 2. Update yomai/streaming/sse.py — Heartbeat

Add a heartbeat coroutine that runs concurrently with the agent loop:

```python
async def heartbeat(queue: asyncio.Queue, interval_secs: int = 15):
    """Enqueue a ping event every interval_secs on idle connections."""
    while True:
        await asyncio.sleep(interval_secs)
        await queue.put(await sse_ping())
```

Integration in `AgentRoute`:
- Use a queue-based response generator: the agent task puts SSE strings into an `asyncio.Queue`, the heartbeat task puts `ping` events into the same queue, and the HTTP generator yields from the queue until a sentinel is received.
- Start `heartbeat()` as an `asyncio.Task` when the stream begins.
- Cancel the heartbeat task when the stream ends (success, disconnect, or error).
- The heartbeat runs concurrently — it does not block the agent loop.

### 3. Update yomai/core/router.py — Disconnect Handling

Detect client disconnect and cancel the LLM stream:

```python
async def handle(self, request: Request) -> StreamingResponse:
    async def generate():
        agent_task = asyncio.create_task(run_agent_loop())
        
        while not agent_task.done():
            if await request.is_disconnected():
                agent_task.cancel()
                # Do NOT save memory — incomplete exchange
                return
            await asyncio.sleep(0.1)
        
        await save_memory()
```

Rules:
- If client disconnects: cancel LLM API call (to avoid burning tokens), do NOT save memory, do NOT log an error (disconnections are normal)
- If agent completes normally: save memory, send `done` event, close stream cleanly
- If agent errors: send `error` SSE event, do NOT save memory, close stream

### 4. Graceful Shutdown

Add SIGTERM handling in `yomai/core/app.py`:

```python
import signal
import asyncio

class Yomai:
    def _setup_signal_handlers(self):
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)
    
    def _handle_sigterm(self):
        # 1. Stop accepting new connections
        # 2. Wait up to 30 seconds for active streams to complete
        # 3. Exit
```

Use a connection counter (`_active_connections: int`) incremented on stream start, decremented on stream end. On SIGTERM, poll until `_active_connections == 0` or 30 seconds elapse.

### 5. Provider Selection at Runtime

Update `yomai/core/app.py` to instantiate the correct provider based on config:

```python
def _build_provider(self, config: LLMConfig) -> LLMProvider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    elif config.provider == "openai":
        return OpenAIProvider(config)
    else:
        raise YomaiConfigError(
            f"Unknown provider: {config.provider!r}. Valid options: 'anthropic', 'openai'"
        )
```

### 6. Final Public API Review — yomai/__init__.py

Verify these exports work correctly:
```python
from yomai import Yomai, tool
from yomai.config import Config, LLMConfig, MemoryConfig, AgentConfig, StreamingConfig, DevConfig
from yomai.memory import MemoryBackend
from yomai.workflow import WorkflowRunner
from yomai.llm import LLMProvider
from yomai.events import AgentStartEvent, AgentDoneEvent, ToolEndEvent, ErrorEvent
from yomai.testing import YomaiTestClient, mock_llm, capture_tools
```

Add `yomai/events.py` if it doesn't exist:
```python
from dataclasses import dataclass

@dataclass
class AgentStartEvent:
    session_id: str
    message: str
    path: str

@dataclass
class AgentDoneEvent:
    session_id: str
    reply: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int

@dataclass
class ToolEndEvent:
    tool_name: str
    args: dict
    result: str
    duration_ms: int

@dataclass
class ErrorEvent:
    error: Exception
    session_id: str
    path: str
```

### 7. Non-Feature Guard Rails

For features deferred to V2, raise a helpful error if a developer tries to use them:

```python
# In MemoryConfig validator:
if backend not in ("dict",):
    raise YomaiConfigError(
        f"Memory backend {backend!r} is not available in V1.\n"
        "  Redis backend ships in V2. See: https://yomai.dev/roadmap"
    )
```

Add similar guards for:
- `mode="async"` on `@app.workflow`
- `cache_ttl` on `@tool` (accepted but ignored with a deprecation warning: "cache_ttl has no effect in V1. Redis-backed caching ships in V2.")

## Final Verification Checklist
Before marking Phase 7 complete, verify:
- [ ] `pip install yomai && yomai new test && cd test && yomai run` works
- [ ] Sending a message to `/__yomai__` playground streams a real response
- [ ] Switching provider from `anthropic` to `openai` requires only a config change
- [ ] `mock_llm` tests pass without any LLM API key
- [ ] SIGTERM drains active connections before exit
- [ ] `GET /__yomai__` returns 404 when `YOMAI_ENV=production`
- [ ] All public API symbols are importable from the paths listed in section 4.3 of the spec
```

---

## Cross-Cutting Rules for All Agents

These rules apply to every phase:

1. **No framework stubs after the owning phase.** Once a phase claims a file, that file must be fully implemented. No `pass`, no `# TODO`, no `raise NotImplementedError` in V1 runtime paths. User-decorated marker functions in examples may use `pass`. Phase 0 may create empty package `__init__.py` files only.

2. **SSE correctness.** Every SSE string must end with `\n\n`. The `data:` field must be valid JSON. Test your SSE output with a real browser before marking a phase done.

3. **Async everywhere.** No blocking I/O on the main thread. All DB/network calls must be `await`-able. Use `asyncio.to_thread` for sync tool functions.

4. **Error events, not crashes.** All per-request exceptions must be caught and emitted as `error` SSE events. The server must never 500 on a per-request error.

5. **Starlette, not FastAPI.** The framework base is Starlette directly. Do not add FastAPI as a dependency.

6. **Public API is frozen.** The symbols in section 4.3 of the spec are the public API. Do not rename them, restructure them, or change their call signatures without a clear reason documented in code.

7. **File structure is law.** Every file must live at the path specified in section 12 of the spec. Do not reorganize the package structure.

---

*Built to spec: Yomai — agents should be easy to build, easy to ship, and easy to debug.*
