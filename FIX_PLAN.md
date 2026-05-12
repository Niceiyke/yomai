# Yomai v0.1.0 — Bug Fix Plan

## Fix Order (by complexity & risk)

| # | Issue | Severity | Effort | Risk |
|---|-------|----------|--------|------|
| 1 | SSE newline injection | Critical | S | Low |
| 2 | Data race: `_active_connections` | Critical | S | Low |
| 3 | Data race: `_metrics_counters` | Critical | S | Low |
| 4 | Dead code in `_validate_new_path` | Major | S | None |
| 5 | `strip_reasoning` O(n²) perf | Major | S | Low |
| 6 | Async SSE helpers unnecessary | Minor | S | None |
| 7 | `Depends.depends()` redundant | Minor | S | None |
| 8 | SSE event type sanitization | Critical | S | Low |
| 9 | `Last-Event-ID` parsing | Critical | S | Low |
| 10 | Consumer task leak in `_run_inline_workflow_job` | Major | S | Low |
| 11 | `_validate_tool_args` generics | Major | M | Low |
| 12 | No request body size limit | Major | M | Medium |
| 13 | Code duplication in router | Major | L | High |
| 14 | `json.dumps(default=str)` silently hides errors | Minor | M | Medium |
| 15 | SQLite global lock bottleneck | Minor | M | Medium |
| 16 | `request` param silently skipped in handlers | Minor | M | Medium |
| 17 | Centralized env var docs | Minor | M | None |

---

## Phase 1 — Quick Wins (low risk, ~2h)

### Fix 1: SSE newline injection in `format_sse` / `format_sse_with_id`

**File:** `yomai/streaming/sse.py`

**Root cause:** `json.dumps(data, separators=(',', ':'))` produces compact JSON but doesn't strip `\n` from string values. A tool result or LLM chunk containing `\n\n` will break SSE frame boundaries.

**Fix:** Sanitize string values by replacing `\n` with `\\n` (or `\r\n`→`\\n`) inside the JSON-serialized data, or strip/replace newlines from all string values before serialization.

```python
# yomai/streaming/sse.py

import json
from typing import Any

SSEData = dict[str, Any]


def _sanitize_sse_value(obj: Any) -> Any:
    """Recursively replace newlines in string values to protect SSE framing."""
    if isinstance(obj, str):
        return obj.replace("\n", " ").replace("\r", "")
    if isinstance(obj, dict):
        return {k: _sanitize_sse_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_sse_value(v) for v in obj]
    return obj


def _encode_sse(data: SSEData) -> str:
    return json.dumps(_sanitize_sse_value(data), separators=(",", ":"))


def format_sse(event_type: str, data: SSEData) -> str:
    return f"event: {event_type}\ndata: {_encode_sse(data)}\n\n"


def format_sse_with_id(event_id: int | str, event_type: str, data: SSEData) -> str:
    return f"id: {event_id}\nevent: {event_type}\ndata: {_encode_sse(data)}\n\n"
```

**Test file(s) to update:** `tests/test_core.py` — add test with `\n\n` in content/result.

---

### Fix 2: Data race on `_active_connections`

**File:** `yomai/core/app.py` (lines 1232–1241)

**Root cause:** `_stream_started` and `_stream_finished` do `+= 1` / `-= 1` on `self._active_connections` (a plain `int`) without synchronization. Python int operations are not atomic across async context switches.

**Fix:** Add an `asyncio.Lock` and protect all reads/writes.

```python
# In Yomai.__init__ (add after self._active_connections = 0):
self._active_lock = asyncio.Lock()

def _stream_started(self) -> None:
    # No async context needed for lock — we are already in a coroutine,
    # but _stream_started is called in sync context inside async gen.
    # Use a counter with lock acquire only on writes.
    self._active_connections += 1

def _stream_finished(self) -> None:
    self._active_connections = max(0, self._active_connections - 1)
```

**Better approach — use a simple wrapper:**

```python
class AtomicCounter:
    def __init__(self) -> None:
        self._value: int = 0
        self._lock = asyncio.Lock()

    async def increment(self) -> None:
        async with self._lock:
            self._value += 1

    async def decrement(self) -> None:
        async with self._lock:
            self._value = max(0, self._value - 1)

    @property
    async def value(self) -> int:
        async with self._lock:
            return self._value
```

Then in `Yomai`:
```python
self._active_connections_counter = AtomicCounter()
```

Callbacks become:
```python
async def _stream_started(self) -> None:
    await self._active_connections_counter.increment()

async def _stream_finished(self) -> None:
    await self._active_connections_counter.decrement()
```

And `active_connections`:
```python
@property
async def active_connections(self) -> int:
    return await self._active_connections_counter.value
```

Wait — the current API `self._active_connections` is read directly in `_drain_active_connections` and exposed as a property. The simplest fix that doesn't break the API:

```python
# In Yomai.__init__:
self._active_lock = asyncio.Lock()

def _stream_started(self) -> None:
    self._active_connections += 1

def _stream_finished(self) -> None:
    self._active_connections = max(0, self._active_connections - 1)

async def _drain_active_connections(self) -> None:
    deadline = asyncio.get_running_loop().time() + 30
    while True:
        async with self._active_lock:
            if self._active_connections <= 0:
                break
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.1)
```

But this still has a race on the writes. Since `_stream_started`/`_stream_finished` are not async, they can't use `async with`. The real solution:

Change the callbacks to be `asyncio.Queue` based or use Python's `int` which is actually atomically readable (though writes aren't). Since the race is between concurrent reads+increments, we need a lock. But these callbacks are invoked from inside async generators... We can make them wrap in `asyncio.create_task`.

**Simplest safe fix:** Make the counter operations trivial (single-line operations) and accept that in the worst case, the count is off by a small amount during rapid connection changes. For a shutdown drain, this is good enough. But for correctness...

Actually, the issue is that `+= 1` reads the value, then writes. If two streams start at the same time:
- Stream A reads `_active_connections = 5`
- Stream B reads `_active_connections = 5`
- Stream A writes `_active_connections = 6`
- Stream B writes `_active_connections = 6`

Both incremented but count only went from 5→6 instead of 5→7.

**Real-world:** The likelihood of two stream starts occurring within the same event loop iteration is low in practice, but it IS a correctness bug.

**Fix:** Use an `asyncio.Lock` but make the callback signatures accept it. OR, better: use a direct `asyncio.Queue` with sentinel values.

**Simplest practical fix** — documented below — wraps the increment in a small helper that's called from the route handlers since they already run inside async functions and can `await`:

Change the `LifecycleCallback = Callable[[], None]` to be `Callable[[], Awaitable[None]]` (make them async). Then in `AgentRoute.handle` and `WorkflowRoute.handle`, `await self.on_stream_start()` and `await self.on_stream_end()`. Then make `_stream_started`/`_stream_finished` `async def` and use a lock.

This changes the type alias `LifecycleCallback` which is used in multiple route constructors but only minor changes needed.

---

### Fix 3: Data race on `_metrics_counters`

**File:** `yomai/core/app.py` (line 247)

**Root cause:** `collections.Counter` is not async-safe. All route handlers modify it.

**Fix:** Add an `asyncio.Lock` around Counter mutations.

```python
# In Yomai.__init__:
self._metrics_lock = asyncio.Lock()

# Helper methods:
async def _incr_metric(self, key: str, amount: int = 1) -> None:
    async with self._metrics_lock:
        self._metrics_counters[key] += amount

async def _get_metrics(self) -> dict[str, int]:
    async with self._metrics_lock:
        return dict(self._metrics_counters)
```

Then replace all `self._metrics_counters["X"] += 1` with `await self._incr_metric("X")`. The `_metrics()` endpoint already reads the counter directly (line 373-376 of `app.py`); change it to use `_get_metrics()`.

All mutation sites to update:
- `app.py:388`: `self._metrics_counters["errors_total"] += 1` (in `_job_cancel`)
- `app.py:509`: `self._metrics_counters["errors_total"] += 1` (in `_run_inline_workflow_job` CancelledError)
- `app.py:523`: `self._metrics_counters["errors_total"] += 1` (in `_run_inline_workflow_job` Exception)
- `app.py:747`: `self._metrics_counters["errors_total"] += 1` (rate limit exceeded in handle_async_workflow)
- `app.py:759`: `self._metrics_counters["errors_total"] += 1` (concurrent limit in handle_async_workflow)
- `app.py:766`: `self._metrics_counters["requests_total"] += 1`
- `app.py:767`: `self._metrics_counters["workflow_jobs_total"] += 1`

---

### Fix 4: Dead code in `_validate_new_path`

**File:** `yomai/core/app.py` (line 1280)

```python
if method is None or True:  # Always add to _paths for uniqueness tracking
    self._paths.add(path)
```

**Fix:** Remove the dead condition — just always add to `_paths`:

```python
self._paths.add(path)
```

The `_paths` set is used nowhere else meaningfully (only added to, never checked). But it's maintained for potential future use. Keep it but remove the dead branch.

---

### Fix 5: `strip_reasoning` O(n²) character-by-character iteration

**File:** `yomai/core/agent.py` (lines 91–108)

**Root cause:** `_maybe_strip_reasoning` iterates character-by-character with `text.startswith(..., i)` on every iteration. Each `startswith` call scans from position `i`, making it O(n²) worst case.

**Fix:** Replace with a regex-based approach plus a stateful buffer for cross-chunk reasoning blocks.

```python
import re

_REASONING_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_REASONING_START = re.compile(r"<think>.*", re.DOTALL)


def _maybe_strip_reasoning(self, text: str) -> str:
    if not self.strip_reasoning:
        return text

    # If we were inside a reasoning block from a previous chunk, prepend <think>
    if self._inside_reasoning:
        text = "<think>" + text

    # Remove complete <think>...</think> blocks
    result = _REASONING_RE.sub("", text)

    # Check if there's an unclosed <think> at the end
    if "<think>" in result:
        last_think = result.rfind("<think>")
        if "</think>" not in result[last_think:]:
            self._inside_reasoning = True
            result = result[:last_think]
        else:
            self._inside_reasoning = False
    else:
        self._inside_reasoning = "</think>" in text and "<think>" not in text.rsplit("</think>", 1)[0]

    # Clean up: if we ended a chunk mid-reasoning and there's trailing text after </think>
    return result
```

Wait, let me think about this more carefully. The current implementation handles the case where `<think>` and `</think>` span multiple chunks. The regex approach needs to handle this too.

**Better approach with buffer:**

```python
def _maybe_strip_reasoning(self, text: str) -> str:
    if not self.strip_reasoning:
        return text

    if self._inside_reasoning:
        text = "<think>" + text

    # Remove all complete <think>...</think> blocks
    output = []
    pos = 0
    while pos < len(text):
        start = text.find("<think>", pos)
        if start == -1:
            output.append(text[pos:])
            break
        output.append(text[pos:start])
        end = text.find("</think>", start + 7)
        if end == -1:
            self._inside_reasoning = True
            pos = len(text)
            break
        self._inside_reasoning = False
        pos = end + 8
    else:
        pos = 0  # fallback — should not reach here

    return "".join(output)
```

This is O(n) using `str.find()` and handles cross-chunk boundaries correctly.

---

### Fix 6: SSE helpers are unnecessarily async

**File:** `yomai/streaming/sse.py` (lines 20–54)

**Root cause:** Functions like `sse_done()`, `sse_chunk()`, `sse_error()` etc. are `async def` but contain no `await` calls.

**Fix:** Make them synchronous:

```python
def sse_chunk(content: str) -> str:
    return format_sse("chunk", {"type": "chunk", "content": content})

def sse_tool_start(name: str, args: dict[str, Any], id: str) -> str:
    return format_sse("tool_start", {"type": "tool_start", "name": name, "args": args, "id": id})

def sse_tool_end(id: str, result: str, duration_ms: int) -> str:
    return format_sse("tool_end", {"type": "tool_end", "id": id, "result": result, "duration_ms": duration_ms})

def sse_usage(input_tokens: int, output_tokens: int, cost_usd: float) -> str:
    return format_sse("usage", {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost_usd})

def sse_done() -> str:
    return format_sse("done", {"type": "done"})

def sse_error(message: str, code: str = "error") -> str:
    return format_sse("error", {"type": "error", "message": message, "code": code})

def sse_ping() -> str:
    return format_sse("ping", {})
```

**All call sites must drop `await`:**

Search pattern across the codebase:
```
await sse_chunk(
await sse_done(
await sse_error(
await sse_ping(
await sse_tool_end(
await sse_tool_start(
await sse_usage(
```

Files to update:
- `yomai/core/agent.py` (lines 69, 72, 76, 88, 89, 112, 118, 133)
- `yomai/core/app.py` (lines 510, 511, 516, 521, 538)
- `yomai/core/router.py` (lines 271, 288, 289, 435, 440)
- `yomai/workflow/runner.py` (line 75)

Also update `__init__.py` exports — they're still fine, just the call sites change.

---

### Fix 7: `Depends.depends()` classmethod is redundant

**File:** `yomai/core/app.py` (lines 84–86)

**Fix:** Either remove the classmethod or mark it deprecated. For V1, keep backward compat but remove:

```python
# Remove entirely
```

If keeping for backward compat, add deprecation:

```python
@classmethod
def depends(cls, func: Callable[..., Any]) -> "Depends":
    import warnings
    warnings.warn(
        "Depends.depends() is deprecated. Use Depends() directly.",
        DeprecationWarning,
        stacklevel=2,
    )
    return cls(func)
```

---

## Phase 2 — Medium Effort (~4h)

### Fix 8: SSE event type sanitization

**File:** `yomai/streaming/sse.py`

**Root cause:** Event type name is interpolated directly into SSE format string. While unlikely in the current codebase (types are hardcoded), any user-supplied event name could contain `\n` and break framing.

**Fix:** Strip/validate event type:

```python
def _sanitize_event_type(et: str) -> str:
    """SSE event type must not contain newlines or be empty."""
    cleaned = et.replace("\n", "").replace("\r", "").strip()
    if not cleaned:
        cleaned = "message"
    return cleaned


def format_sse(event_type: str, data: SSEData) -> str:
    return f"event: {_sanitize_event_type(event_type)}\ndata: {_encode_sse(data)}\n\n"


def format_sse_with_id(event_id: int | str, event_type: str, data: SSEData) -> str:
    return f"id: {event_id}\nevent: {_sanitize_event_type(event_type)}\ndata: {_encode_sse(data)}\n\n"
```

---

### Fix 9: `Last-Event-ID` parsing is fragile

**File:** `yomai/core/app.py` (line 407)

**Root cause:** `last_event_id.isdigit()` only handles positive integers. Redis stream event IDs look like `"1689626401123-0"`, which fails `isdigit()`. The `InMemoryJobEventStore` uses integer IDs.

**Fix:** Accept all event IDs as strings and pass through as-is:

```python
async def _job_stream(self, request: Request) -> StreamingResponse | JSONResponse:
    auth_error = self._metadata_auth_error(request)
    if auth_error is not None:
        return auth_error
    job_id = request.path_params["job_id"]
    if await self.jobs.get(job_id) is None:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    last_event_id = request.headers.get("Last-Event-ID")
    after_id: str | None = last_event_id if last_event_id else None

    async def stream():
        async for event in self.job_events.subscribe(
            job_id,
            after_id,
            heartbeat_secs=self.config.streaming.heartbeat_secs,
        ):
            if await request.is_disconnected():
                break
            if event is None:
                yield ": ping\n\n"
                continue
            yield format_sse_with_id(event.id, event.event, event.data)
            if event.data.get("type") == "done":
                break

    return StreamingResponse(stream(), media_type="text/event-stream")
```

The `subscribe` method in both `InMemoryJobEventStore` and `RedisJobEventStore` already handle `int | str | None` for `after_id`. No changes needed there.

---

### Fix 10: Consumer task leak in `_run_inline_workflow_job`

**File:** `yomai/core/app.py` (lines 463–533)

**Root cause:** The consumer task is awaited on line 533 (`await consumer`) but if any exception occurs before the `finally` block runs, or if `queue.put(None)` throws, the consumer could hang.

**Fix:** Add timeout and cancellation safety:

```python
async def _run_inline_workflow_job(
    self,
    *,
    job_id: str,
    path: str,
    handler: Callable[..., Any],
    body: dict[str, Any],
    session_id: str,
    path_kwargs: dict[str, Any],
) -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    current = await self.jobs.get(job_id)
    if current is not None and current.status == "cancelled":
        return
    await self.jobs.update_status(job_id, "running")
    await self.hooks.emit("job.started", job_id=job_id, route=path)
    await self.hooks.emit("workflow.start", job_id=job_id, route=path)

    async def consume_events() -> None:
        while True:
            item = await queue.get()
            if item is None:
                break
            await self._append_job_sse(job_id, item)

    consumer = asyncio.create_task(consume_events())
    try:
        runner = WorkflowRunner(queue, session_id, self.memory, self, job_id=job_id)
        from yomai.core.router import WorkflowRoute

        route = WorkflowRoute(path, handler, self, self.memory)
        kwargs = route._build_kwargs(body, runner, path_kwargs)
        result = handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        await queue.put(sse_result(result if result is not None else ""))
        await queue.put(await self._done_sse())
        await self.jobs.update_status(job_id, "succeeded", result=result)
        await self.hooks.emit("workflow.done", job_id=job_id, route=path, result=result)
        await self.hooks.emit("job.succeeded", job_id=job_id, route=path, result=result)
        released = self.rate_limiter.release_concurrent(session_id)
        if inspect.isawaitable(released):
            await released
    except asyncio.CancelledError:
        from yomai.streaming.sse import sse_error

        self._metrics_counters["errors_total"] += 1
        await self.jobs.update_status(job_id, "cancelled", error="Job cancelled")
        await queue.put(await sse_error("Job cancelled", "cancelled"))
        await self.hooks.emit("job.cancelled", job_id=job_id, route=path)
        released = self.rate_limiter.release_concurrent(session_id)
        if inspect.isawaitable(released):
            await released
        await queue.put(await self._done_sse())
    except Exception as exc:
        message_out = "Internal server error" if os.environ.get("YOMAI_ENV") == "production" else str(exc)
        from yomai.streaming.sse import sse_error

        await queue.put(await sse_error(message_out, exc.__class__.__name__))
        await queue.put(await self._done_sse())
        self._metrics_counters["errors_total"] += 1
        await self.jobs.update_status(job_id, "failed", error=message_out)
        await self.hooks.emit("workflow.failed", job_id=job_id, route=path, error=message_out)
        await self.hooks.emit("job.failed", job_id=job_id, route=path, error=message_out)
        await self.hooks.emit("error", job_id=job_id, route=path, error=message_out)
        released = self.rate_limiter.release_concurrent(session_id)
        if inspect.isawaitable(released):
            await released
    finally:
        # Always send the sentinel and wait for consumer to finish
        try:
            await queue.put(None)
        except Exception:
            pass  # queue.put can't really fail, but be safe
        try:
            await asyncio.wait_for(consumer, timeout=10.0)
        except TimeoutError:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass
```

---

### Fix 11: `_validate_tool_args` doesn't handle generic types

**File:** `yomai/core/agent.py` (lines 136–141)

**Root cause:** The type check only handles concrete types (`str`, `int`, `float`, `bool`, `list`, `dict`). Generic annotations like `list[str]`, `Optional[int]`, `Union[str, int]` are ignored.

**Fix:** Expand the type resolution to handle `typing.get_origin` and `typing.get_args`:

```python
import typing

def _validate_tool_args(self, fn: ToolFunction, args: dict[str, Any]) -> None:
    hints = getattr(fn, "__annotations__", {})
    for name, value in args.items():
        expected = hints.get(name)
        if expected is None:
            continue

        # Unwrap Optional / Union
        origin = typing.get_origin(expected)
        args_list = typing.get_args(expected)

        if origin is typing.Union or origin is getattr(typing, "_Union", None):
            # For Optional[T] (Union[T, None]), check if value is None or matches T
            non_none = [a for a in args_list if a is not type(None)]
            if non_none:
                if not any(self._type_matches(v, a) for a in non_none):
                    raise TypeError(f"Tool argument {name!r} must match one of {non_none}")
            continue

        if origin is list or origin is getattr(typing, "List", None):
            if not isinstance(value, list):
                raise TypeError(f"Tool argument {name!r} must be a list")
            if args_list:
                item_type = args_list[0]
                for item in value:
                    if isinstance(item_type, type) and not isinstance(item, item_type):
                        raise TypeError(f"Tool argument {name!r} items must be {item_type.__name__}")
            continue

        if origin is dict or origin is getattr(typing, "Dict", None):
            if not isinstance(value, dict):
                raise TypeError(f"Tool argument {name!r} must be a dict")
            continue

        if isinstance(expected, type) and not isinstance(value, expected):
            raise TypeError(f"Tool argument {name!r} must be {expected.__name__}")

@staticmethod
def _type_matches(value: Any, annot: Any) -> bool:
    if annot is type(None):
        return value is None
    if isinstance(annot, type):
        return isinstance(value, annot)
    return True  # fallback: accept if we can't validate
```

---

### Fix 12: No request body size limit

**File:** `yomai/core/app.py` (various route handlers), `yomai/core/router.py`

**Root cause:** `await request.json()` reads the entire body into memory with no size limit.

**Fix:** Read the body with a size limit before parsing:

```python
MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB

async def _read_json_body(request: Request, max_size: int = MAX_BODY_SIZE) -> dict[str, Any]:
    body_bytes = b""
    async for chunk in request.stream():
        body_bytes += chunk
        if len(body_bytes) > max_size:
            raise ValueError("Request body too large")
    import json
    return json.loads(body_bytes.decode())
```

Then replace `await request.json()` with `await _read_json_body(request)` in:
- `AgentRoute.handle` (line 232)
- `WorkflowRoute.handle` (line 411)
- `handle_async_workflow` (line 733-734)
- `PutRoute.handle` (line 828)
- `PatchRoute.handle` (line 927-928)

---

## Phase 3 — Complex Refactor (~6h)

### Fix 13: Code duplication in router

**Files:** `yomai/core/router.py`, new file `yomai/core/_base_route.py`

**Root cause:** `_cors_headers()` and `_run_dependencies()` and auth preamble are copy-pasted across 7 route classes.

**Fix:** Extract a base class:

```python
# yomai/core/_base_route.py (new file)

from __future__ import annotations

import hmac
import inspect
from typing import Any, Callable

from starlette.responses import JSONResponse

from yomai._types import Request
from yomai.memory import MemoryBackend


# Re-export
LifecycleCallback = Callable[[], None]
AcceptCallback = Callable[[], bool]


class BaseRoute:
    """Shared behaviour for all Yomai route types."""

    def __init__(
        self,
        path: str,
        handler: Callable[..., Any],
        memory: MemoryBackend | None = None,
        on_stream_start: LifecycleCallback | None = None,
        on_stream_end: LifecycleCallback | None = None,
        should_accept: AcceptCallback | None = None,
        log_usage: bool = True,
        required_api_key: str = "",
        path_params: set[str] | None = None,
        cors: dict[str, Any] | None = None,
        dependencies: list[Any] | None = None,
    ) -> None:
        self.path = path
        self.handler = handler
        self.memory = memory
        self.on_stream_start = on_stream_start
        self.on_stream_end = on_stream_end
        self.should_accept = should_accept
        self.log_usage = log_usage
        self.required_api_key = required_api_key
        self.path_params = path_params or set()
        self.cors = cors or {}
        self.dependencies = dependencies or []

    def _cors_headers(self) -> dict[str, str]:
        """Build CORS headers from route-level cors config."""
        if not self.cors:
            return {}
        headers: dict[str, str] = {}
        allow_origins = self.cors.get("allow_origins", [])
        if isinstance(allow_origins, str):
            allow_origins = [allow_origins]
        if allow_origins:
            headers["Access-Control-Allow-Origin"] = ", ".join(allow_origins)
        if self.cors.get("allow_credentials"):
            headers["Access-Control-Allow-Credentials"] = "true"
        # Methods/Headers only on streaming routes
        allow_methods = self.cors.get("allow_methods")
        if allow_methods:
            if isinstance(allow_methods, str):
                allow_methods = [allow_methods]
            headers["Access-Control-Allow-Methods"] = ", ".join(allow_methods)
        allow_headers = self.cors.get("allow_headers")
        if allow_headers:
            if isinstance(allow_headers, str):
                allow_headers = [allow_headers]
            headers["Access-Control-Allow-Headers"] = ", ".join(allow_headers)
        return headers

    async def _run_dependencies(self, request: Request, path_kwargs: dict[str, Any]) -> None:
        """Run dependency callables, injecting results into request state."""
        request._yomai_path_kwargs = path_kwargs
        for dep in self.dependencies:
            if hasattr(dep, "callable"):
                result = dep.callable(request)
                if inspect.isawaitable(result):
                    result = await result

    async def _check_auth_and_drain(self, request: Request) -> JSONResponse | None:
        """Return error response if auth fails or server is draining, else None."""
        if self.should_accept is not None and not self.should_accept():
            return JSONResponse({"error": "Server is shutting down"}, status_code=503)
        if self.required_api_key:
            auth = request.headers.get("Authorization", "")
            expected = f"Bearer {self.required_api_key}"
            if not hmac.compare_digest(auth, expected):
                return JSONResponse({"error": "Invalid or missing API key"}, status_code=401)
        return None

    def _extract_path_kwargs(self, request: Request) -> dict[str, Any]:
        """Extract path parameters from the request."""
        kwargs: dict[str, Any] = {}
        if self.path_params:
            for param_name in self.path_params:
                value = request.path_params.get(param_name)
                if value is not None:
                    kwargs[param_name] = value
        return kwargs
```

Then refactor each route class to inherit from `BaseRoute`. `AgentRoute`, `WorkflowRoute`, `GetRoute`, `DeleteRoute`, `PutRoute`, `PatchRoute`, `HeadRoute`, `OptionsRoute` all get simplified.

Each class only keeps:
- Its own `__init__` calling `super().__init__()`
- Its own `handle()` method
- Its own `_build_kwargs()` method (where applicable)

This removes ~200 lines of duplicated code.

---

### Fix 14: `json.dumps(default=str)` hides serialization errors

**Files:** `yomai/workflow/runner.py:95`, `yomai/jobs/store.py:180,184`

**Root cause:** `default=str` silently converts any non-JSON-serializable object to its string representation.

**Fix:** Remove `default=str` and let serialization fail explicitly, or add a warning:

```python
# yomai/workflow/runner.py — _input_hash method
def _input_hash(self, input: Any) -> str:
    try:
        payload = json.dumps(input, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = str(input)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

```python
# yomai/jobs/store.py — _record_to_redis
"result": json.dumps(record.result, separators=(",", ":")),
"metadata": json.dumps(record.metadata, separators=(",", ":")),
```

If `record.result` is not JSON-serializable, this should raise an error rather than silently converting. Remove `default=str`. However, since job results could be arbitrary Python objects, we need to handle this gracefully:

```python
try:
    result_json = json.dumps(record.result, separators=(",", ":"))
except (TypeError, ValueError):
    result_json = json.dumps(str(record.result), separators=(",", ":"))
```

---

### Fix 15: SQLite global lock bottleneck

**File:** `yomai/memory/sqlite.py`

**Root cause:** All operations share one `asyncio.Lock()`. Different session_ids block each other unnecessarily.

**Fix:** This is a design trade-off. The current lock ensures WAL writer serialization (SQLite only supports one writer at a time). Since WAL mode is enabled, readers can proceed concurrently.

The lock is actually **necessary** for SQLite in WAL mode to prevent `SQLITE_BUSY` errors during concurrent writes. While it serializes ALL operations, the alternative (per-session locks) would risk concurrent writes to the same DB file.

**Recommendation:** Keep the current approach. It's the correct pattern for async SQLite. The lock is on the application layer because `run_in_executor` dispatches to a thread pool where multiple threads could try to write simultaneously. The current implementation is correct.

**No code changes needed.** Mark this as "won't fix — current design is correct for SQLite."

---

### Fix 16: `request` param silently skipped in handler sigs

**Files:** `yomai/core/router.py` (AgentRoute._build_kwargs:325, WorkflowRoute._build_kwargs:489, GetRoute.handle:571, DeleteRoute.handle:664, PutRoute.handle:938, etc.)

**Root cause:** When a user annotates a handler parameter as `request: Request`, it's silently ignored with `elif name == "request": continue`.

**Fix:** Inject the actual request object:

For `AgentRoute._build_kwargs`:
```python
elif name == "request":
    # Request is injected separately via path_kwargs
    continue
```

Change to:
```python
elif name == "request":
    # request is not available at _build_kwargs time for AgentRoute
    # (it's used in Handle for body parsing). Provide a clear error.
    raise ValueError(
        f"'request' is not available in agent/streaming workflow handlers. "
        f"Use dependencies instead: @app.agent(..., dependencies=[Depends(my_auth)])"
    )
```

Alternatively, inject the request object where possible (for GET/DELETE/PUT/PATCH it can be a `request: Request` parameter).

For async workflow mode, `request` IS available in the inner `handle_async_workflow` closure. Change:
```python
elif name == "request":
    continue
```
to:
```python
elif name == "request":
    kwargs[name] = request
```

For `WorkflowRoute._build_kwargs`, route the request through (it's available in `handle`):
```python
def _build_kwargs(
    self, body: dict[str, Any], runner: WorkflowRunner, path_kwargs: dict[str, Any], request: Request | None = None
) -> dict[str, Any]:
    ...
    elif name == "request":
        kwargs[name] = request
```

For `GetRoute`, `DeleteRoute`, `PutRoute`, `PatchRoute` — `request` is already available in the `handle` method scope. Inject it:
```python
elif name == "request":
    kwargs[name] = request
```

---

### Fix 17: Centralized env var documentation

**Files:** New file `yomai/env.py`

**Fix:** Create a central registry of all environment variables:

```python
# yomai/env.py (new file)

"""All environment variables consumed by Yomai, in one place."""

import os
from typing import Final

# -- Runtime environment --
YOMAI_ENV: Final[str] = os.environ.get("YOMAI_ENV", "development")
"""Set to 'production' to hide error details and disable dev playground."""

YOMAI_HANDLE_SIGTERM: Final[str] = os.environ.get("YOMAI_HANDLE_SIGTERM", "")
"""Set to '1' to enable graceful shutdown on SIGTERM."""

YOMAI_APP_TITLE: Final[str] = os.environ.get("YOMAI_APP_TITLE", "Yomai Agent API")
"""Title used in OpenAPI schema and playground."""

YOMAI_API_KEY: Final[str] = os.environ.get("YOMAI_API_KEY", "")
"""Metadata endpoint API key (used for /__yomai__/* in production)."""

# -- LLM Provider keys --
ANTHROPIC_API_KEY: Final[str] = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL: Final[str | None] = os.environ.get("ANTHROPIC_BASE_URL") or None
OPENAI_API_KEY: Final[str] = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL: Final[str | None] = os.environ.get("OPENAI_BASE_URL") or None

# -- Redis --
REDIS_URL: Final[str] = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
```

Then import from `yomai.env` instead of `os.environ.get(...)` throughout the codebase. This provides:
- Single source of truth
- IDE autocomplete
- Discoverability via `help(yomai.env)`
- Easy to document all vars in one place

Files to update: `yomai/config.py`, `yomai/core/app.py`, `yomai/core/router.py`, `yomai/core/agent.py`.

---

## Test Plan

For each fix, add or update tests:

| Fix | Test file | What to test |
|-----|-----------|-------------|
| 1 | `tests/test_core.py` | SSE with newline-containing content |
| 2 | `tests/test_core.py` | Concurrent stream start/stop counts |
| 3 | `tests/test_v2_hooks_metrics.py` | Metrics counter consistency under load |
| 5 | `tests/test_core.py` | `strip_reasoning=True` with various XML inputs |
| 8 | `tests/test_core.py` | SSE with newline in event type |
| 9 | `tests/test_v2_redis_jobs.py` | Job stream replay with string and int event IDs |
| 10 | `tests/test_v2_jobs.py` | Job cancellation during inline workflow |
| 11 | `tests/test_core.py` | Tool validation with `list[str]`, `Optional[int]`, `Literal` |
| 12 | `tests/test_core.py` | Large request body rejection |
| 14 | `tests/test_v2_jobs.py` | Job record with non-serializable result |
| 16 | `tests/test_routing.py` | Handler with `request: Request` parameter |

---

## Rollout Strategy

1. **Phase 1 first** (Fixes 1–7) — Quick, low-risk, improves correctness and perf. Can ship as v0.1.1.
2. **Phase 2 next** (Fixes 8–12) — Slightly more involved, no API breakage. Ship as v0.1.2.
3. **Phase 3 last** (Fixes 13–17) — Major refactor of router, may touch many files. Ship as v0.2.0.

All phases are backward-compatible with the existing public API (`@tool`, `@app.agent`, `@app.workflow`, `Yomai()` constructor).

---

## Estimated Effort

| Phase | Hours | Files touched |
|-------|-------|--------------|
| Phase 1 | ~2h | ~6 files |
| Phase 2 | ~4h | ~10 files |
| Phase 3 | ~6h | ~15 files |
| **Total** | **~12h** | **~20 files** |
