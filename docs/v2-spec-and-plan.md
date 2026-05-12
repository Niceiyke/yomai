# Yomai V2 Specification and Build Plan

> Theme: make Yomai production-ready while keeping the V1 developer API simple.
>
> V2 must preserve V1 code compatibility. Existing `@app.agent`, `@app.workflow`, `@tool`, `RouteGroup`, `Depends`, memory, testing, OpenAPI, and SSE behavior continue to work.

---

## 1. V2 Goals

1. Durable async workflow execution.
2. Horizontal scaling through Redis-backed shared state.
3. Reconnectable SSE streams for long-running jobs.
4. Production controls: rate limits, budget limits, cancellation, retries, metrics.
5. Lifecycle hooks for analytics, tracing, auditing, and error reporting.
6. Better memory strategies for long conversations.
7. Optional queue integration, preferably through swiftQ as the first backend.

## 2. Non-Goals for V2

These should remain deferred unless the production runtime is finished first:

- WebSocket transport.
- Voice/audio streaming.
- Image input.
- Multi-agent orchestration protocol.
- Hosted deployment platform.
- Complex LangGraph-style state-machine authoring.

---

## 3. Public API Additions

### 3.1 Queue Configuration

```python
from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig

app = Yomai(
    llm=LLMConfig(provider="anthropic"),
    memory=MemoryConfig(backend="redis", url="redis://localhost:6379/0"),
    queue=QueueConfig(
        backend="swiftq",
        url="redis://localhost:6379/0",
        signing_key="change-me",
        default_queue="default",
        job_ttl_secs=86400,
        event_ttl_secs=86400,
    ),
)
```

`QueueConfig.backend` values:

- `"none"` — default, disables async workflow jobs.
- `"inline"` — executes jobs synchronously for tests/dev.
- `"swiftq"` — V2 default production queue backend.

Future adapters may support `"arq"`, `"rq"`, `"celery"`, or `"taskiq"`.

### 3.2 Async Workflows

```python
@app.workflow("/research", mode="async")
async def research(topic: str, runner: WorkflowRunner) -> dict[str, str]:
    outline = await runner.step("outline", make_outline, topic)
    summary = await runner.step("summary", summarize, outline)
    return {"summary": summary}
```

Request behavior:

```http
POST /research
Content-Type: application/json

{"topic": "AI agents"}
```

Response:

```http
202 Accepted
Content-Type: application/json

{
  "job_id": "job_abc123",
  "status_url": "/__yomai__/jobs/job_abc123",
  "stream_url": "/__yomai__/jobs/job_abc123/stream"
}
```

### 3.3 Job Endpoints

```text
GET    /__yomai__/jobs/{job_id}
GET    /__yomai__/jobs/{job_id}/stream
POST   /__yomai__/jobs/{job_id}/cancel
DELETE /__yomai__/jobs/{job_id}
```

`GET /__yomai__/jobs/{job_id}` returns:

```json
{
  "id": "job_abc123",
  "route": "/research",
  "status": "running",
  "created_at": "2026-05-12T10:00:00Z",
  "started_at": "2026-05-12T10:00:01Z",
  "finished_at": null,
  "attempts": 1,
  "stream_url": "/__yomai__/jobs/job_abc123/stream",
  "result": null,
  "error": null
}
```

Job statuses:

```python
JobStatus = Literal[
    "queued",
    "running",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
    "expired",
]
```

### 3.4 Reconnectable SSE

Every queued workflow event has an SSE `id`.

```text
id: 17
event: step_done
data: {"type":"step_done","name":"search","duration_ms":1200}
```

Clients reconnect with:

```http
GET /__yomai__/jobs/job_abc123/stream
Last-Event-ID: 17
```

The server replays events after id `17`, then continues streaming live events.

### 3.5 Hooks

```python
@app.on("agent.start")
async def agent_start(event):
    ...

@app.on("tool.end")
async def tool_end(event):
    ...

@app.on("workflow.step.done")
async def step_done(event):
    ...

@app.on("job.failed")
async def job_failed(event):
    ...
```

Initial hook names:

- `agent.start`
- `agent.done`
- `tool.start`
- `tool.end`
- `workflow.start`
- `workflow.step.start`
- `workflow.step.done`
- `workflow.done`
- `job.queued`
- `job.started`
- `job.retrying`
- `job.cancelled`
- `job.failed`
- `job.succeeded`
- `error`

Hooks must not break the response stream. Hook failures are logged and optionally emitted to `error` hooks.

### 3.6 Rate Limits

```python
from yomai.config import RateLimitConfig

app = Yomai(
    rate_limits=RateLimitConfig(
        requests_per_minute=60,
        max_concurrent_per_session=3,
        tokens_per_day=100_000,
    )
)
```

When exceeded:

```text
event: error
data: {"type":"error","code":"rate_limited","message":"Rate limit exceeded","retry_after":30}
```

### 3.7 Budget Limits

```python
from yomai.config import BudgetConfig

app = Yomai(
    budgets=BudgetConfig(
        max_tokens_per_request=10_000,
        max_tokens_per_session=100_000,
        max_cost_per_request=0.10,
        max_cost_per_day=25.00,
        on_exceeded="stop",  # "stop" | "warn"
    )
)
```

### 3.8 Tool Result Caching

```python
@tool(cache_ttl=300)
async def get_weather(city: str) -> str:
    ...
```

Cache key is derived from:

```text
tool name + stable JSON serialization of args + optional version
```

### 3.9 Memory Strategies

```python
@app.agent("/chat", memory_strategy="summarize")
async def chat(message: str, session_id: str):
    ...
```

Initial strategies:

- `truncate` — existing behavior.
- `summarize` — summarize older history into a compact system note.
- `none` — no memory for this route.

Later strategies:

- `semantic` — vector retrieval.
- `hybrid` — summary + recent messages + semantic recall.

---

## 4. Internal Architecture

### 4.1 New Packages

```text
yomai/
├── queue/
│   ├── base.py
│   ├── swiftq.py
│   ├── inline.py
│   └── store.py
├── jobs/
│   ├── models.py
│   ├── routes.py
│   ├── events.py
│   └── runner.py
├── hooks/
│   ├── registry.py
│   └── events.py
├── limits/
│   ├── rate.py
│   └── budget.py
└── memory/
    ├── redis.py
    └── strategies.py
```

### 4.2 Queue Adapter Interface

```python
class QueueBackend(Protocol):
    async def enqueue_workflow(
        self,
        *,
        job_id: str,
        route: str,
        payload: dict[str, Any],
        session_id: str | None,
        headers: dict[str, str],
    ) -> str: ...

    async def cancel(self, job_id: str) -> None: ...
    async def get_status(self, job_id: str) -> JobRecord | None: ...
```

Yomai should not expose swiftQ directly in the public API. swiftQ is an implementation detail behind `QueueConfig(backend="swiftq")`.

### 4.3 Job Event Store

Yomai owns the append-only event log. This keeps SSE semantics inside Yomai instead of coupling swiftQ to Yomai-specific events.

Minimum interface:

```python
class JobEventStore(Protocol):
    async def append(self, job_id: str, event: SSEEvent) -> int: ...
    async def read_after(self, job_id: str, event_id: int | None) -> list[StoredEvent]: ...
    async def subscribe(self, job_id: str, after_id: int | None) -> AsyncIterator[StoredEvent]: ...
```

Redis implementation should use Redis Streams if available:

```text
XADD yomai:jobs:{job_id}:events * event chunk data '{...}'
XREAD BLOCK 15000 STREAMS yomai:jobs:{job_id}:events {last_id}
```

Inline/test implementation may use in-memory lists and asyncio conditions.

### 4.4 Workflow Checkpoints

Every `runner.step()` persists a checkpoint:

```json
{
  "job_id": "job_abc123",
  "step": "search",
  "input_hash": "sha256:...",
  "status": "succeeded",
  "result": "...",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 1200
}
```

On retry/restart:

1. Load checkpoints for the job.
2. If the same step/input hash has succeeded, return the stored result.
3. Otherwise execute the step and save a new checkpoint.

---

## 5. swiftQ Integration Specification

### 5.1 Dependency Strategy

Make queue support optional:

```toml
[project.optional-dependencies]
queue = ["swiftq[redis]>=0.1"]
```

Base Yomai install remains lightweight.

### 5.2 Queue Creation

```python
from swiftq import Queue

queue = Queue.redis(
    config.queue.url,
    signing_key=config.queue.signing_key,
    prefix=config.queue.prefix or "yomai:swiftq",
    result_ttl=config.queue.job_ttl_secs,
)
```

### 5.3 Hidden Workflow Task

Yomai registers an internal task:

```python
@queue.task(
    name="yomai.workflow.run",
    retries=config.queue.retries,
    retry_delay=config.queue.retry_delay_secs,
    timeout=config.queue.timeout_secs,
)
def run_workflow_job(job_id: str, route: str, payload: dict[str, Any]) -> None:
    ...
```

If swiftQ adds native async workers, this can become:

```python
@queue.task(name="yomai.workflow.run")
async def run_workflow_job(...):
    ...
```

### 5.4 CLI

Yomai exposes a wrapper command:

```bash
yomai worker app:app --queue default --concurrency 4
```

Internally, it imports the Yomai app, initializes the swiftQ queue, registers internal workflow tasks, and starts swiftQ workers.

---

## 6. Itemized Features to Add to swiftQ for Better Yomai Support

These are swiftQ improvements that would make the Yomai V2 queue integration cleaner and more production-ready.

### 6.1 Must-Have for Yomai V2

1. **Native async worker mode**
   - Run `async def` tasks on a persistent event loop instead of wrapping each task with `asyncio.run()`.
   - Allow many async LLM/tool calls to share one event loop per worker process/thread.

2. **Append-only job event stream API**
   - Add backend methods:
     ```python
     append_event(job_id: str, event: dict[str, Any]) -> str
     read_events(job_id: str, after_id: str | None = None, limit: int = 100) -> list[dict]
     stream_events(job_id: str, after_id: str | None = None, timeout: float | None = None)
     ```
   - Redis backend can use Redis Streams.
   - Memory/inline backend can use list + condition variable.

3. **Async result/status API**
   - Add async equivalents for status and result operations:
     ```python
     await job.aget()
     await job.ainfo()
     await queue.aget_job(job_id)
     ```

4. **Structured cancellation signal for running tasks**
   - Current `Job.cancel()` sets status, but running tasks need a way to observe cancellation.
   - Add:
     ```python
     cancellation_requested()
     raise_if_cancelled()
     ```
   - Worker should periodically check cancellation before/after progress updates and before retries.

5. **Progress event persistence**
   - `set_progress()` should optionally append a progress event to the job event stream, not only update latest status.

6. **Importable worker runner API**
   - Yomai needs to start workers programmatically, not only through `swiftq worker` CLI.
   - Add stable API:
     ```python
     queue.run_worker(queue="default", concurrency=4, with_scheduler=False)
     ```
   - Existing `Queue.work()` may be enough, but document it as stable public API.

7. **Task context metadata**
   - Expose current job context:
     ```python
     current_job()
     current_job_id()
     current_task_name()
     current_queue_name()
     current_attempt()
     ```
   - Yomai uses this for tracing and job event correlation.

### 6.2 Should-Have for Yomai V2

8. **Job metadata updates without overwriting status fields**
   - Provide explicit metadata patching:
     ```python
     backend.patch_metadata(job_id, metadata)
     ```

9. **Result serialization hooks**
   - Allow custom serialization for Pydantic models and Yomai workflow results.
   - Keep JSON as default and avoid pickle.

10. **Heartbeat event hooks**
    - Emit structured events when worker heartbeat is missed, recovered, or stale jobs are requeued.

11. **Graceful drain mode**
    - Stop accepting new jobs, finish current jobs, then exit.
    - Useful for deploys.

12. **Queue pause/resume API**
    - Needed for operational control:
      ```python
      queue.pause("default")
      queue.resume("default")
      queue.is_paused("default")
      ```

13. **Job logs API**
    - Optional append-only logs per job:
      ```python
      append_log(job_id, level="info", message="...")
      read_logs(job_id)
      ```

14. **Dead-letter metadata enrichment**
    - Include traceback hash, error type, attempt count, worker id, queue, and payload size.

15. **Worker capability tags**
    - Worker advertises tags like `llm`, `gpu`, `browser`, `tools`.
    - Queue routing can later use these.

### 6.3 Nice-to-Have Later

16. **True async Redis backend**
    - Use `redis.asyncio` for async worker mode.

17. **OpenTelemetry hooks**
    - Emit spans for enqueue, dequeue, execute, retry, fail, ack.

18. **Cron/scheduler locking hardening**
    - Stronger distributed lock behavior for multiple schedulers.

19. **Job dependency primitives**
    - Chains where task B receives result from task A.
    - Groups with fan-in completion callbacks.

20. **Per-job resource hints**
    - CPU/memory/time/network hints for future smarter worker scheduling.

21. **Backpressure controls**
    - Reject enqueue when queue depth exceeds configured limits.

22. **Built-in Prometheus metrics**
    - Queue depth, job duration, retries, failures, dead letters, worker count.

23. **Admin HTTP API**
    - Optional minimal API for queues, workers, jobs, retries, cancellation, and dead letters.

---

## 7. Build Plan

### Phase 0 — V2 Design Lock

Deliverables:

- Finalize `QueueConfig`, `RateLimitConfig`, `BudgetConfig`, and Redis memory config.
- Decide if swiftQ is a hard optional dependency under `yomai[queue]`.
- Document V1 compatibility guarantees.

Acceptance criteria:

- `pyright` accepts new config types.
- README has a short V2 preview section.
- Existing tests pass unchanged.

### Phase 1 — Redis Memory Backend

Deliverables:

- `RedisMemory` implementing `MemoryBackend`.
- TTL and max-message truncation parity with dict/sqlite memory.
- Redis connection config and tests.

Acceptance criteria:

- Sessions persist across app instances.
- TTL eviction works.
- Existing memory tests pass for Redis variant.

### Phase 2 — Job Models and Event Store

Deliverables:

- `JobRecord`, `JobStatus`, `StoredEvent` models.
- In-memory job store for tests.
- Redis job store/event stream implementation.
- `/__yomai__/jobs/{job_id}` status endpoint.

Acceptance criteria:

- Events can be appended and replayed after a given event id.
- Job status endpoint is protected by production metadata auth.

### Phase 3 — swiftQ Queue Adapter

Deliverables:

- `QueueBackend` protocol.
- `InlineQueueBackend`.
- `SwiftQQueueBackend`.
- Internal `yomai.workflow.run` task.
- `yomai worker` CLI wrapper.

Acceptance criteria:

- Async workflow request returns `202` with job URLs.
- Worker executes the workflow and stores result/status.
- Inline backend makes tests deterministic.

### Phase 4 — Reconnectable SSE for Jobs

Deliverables:

- `GET /__yomai__/jobs/{job_id}/stream`.
- `Last-Event-ID` replay support.
- Heartbeats while waiting for new events.
- Final `done` replay for completed jobs.

Acceptance criteria:

- Client can disconnect and reconnect without losing workflow events.
- Tests verify replay after a middle event id.

### Phase 5 — Workflow Checkpointing

Deliverables:

- Step checkpoint store.
- `WorkflowRunner.step()` checkpoint load/save.
- Retry/resume behavior.

Acceptance criteria:

- Retried workflow does not re-run successful completed steps.
- Input hash mismatch causes a step to run again.

### Phase 6 — Cancellation, Retries, and Dead Letters

Deliverables:

- Cancel endpoint.
- Job cancellation events.
- Retry metadata surfaced in job status.
- Dead-letter visibility in metrics.

Acceptance criteria:

- Queued jobs can be cancelled before execution.
- Running jobs observe cancellation at safe points.
- Failed jobs expose error metadata without leaking secrets in production.

### Phase 7 — Hooks

Deliverables:

- Hook registry.
- Hook event models.
- Fire hooks for agents, tools, workflows, jobs, and errors.

Acceptance criteria:

- Hooks run without blocking streams.
- Hook failures are logged and do not break job execution.

### Phase 8 — Rate Limits and Budgets

Deliverables:

- Redis-backed rate limiter.
- Per-session, per-route, and global limits.
- Token/cost budget checks.

Acceptance criteria:

- Exceeded limits emit standard `error` events.
- Rate limits work across multiple app instances.

### Phase 9 — Tool Caching and Parallel Tool Calls

Deliverables:

- `@tool(cache_ttl=...)`.
- Redis tool cache.
- Concurrent execution for multiple tool calls in one model turn.

Acceptance criteria:

- Cached tool calls do not execute the function.
- Parallel tool calls stream start/end events independently.
- Tool result messages are fed back to the LLM in deterministic order.

### Phase 10 — Metrics and Docs

Deliverables:

- `/__yomai__/metrics` endpoint.
- Queue depth, workers, jobs, tokens, costs, errors, tool calls.
- Updated README and docs.
- Migration guide from V1 to V2.

Acceptance criteria:

- Metrics endpoint is auth-protected in production.
- Docs include examples for async workflows, workers, Redis memory, cancellation, and reconnect.

---

## 8. Testing Plan

Required test categories:

1. Existing V1 regression tests.
2. Redis memory integration tests.
3. Inline queue tests.
4. swiftQ adapter tests.
5. Job event replay tests.
6. Async workflow request/worker/stream tests.
7. Workflow checkpoint retry tests.
8. Cancellation tests.
9. Rate limit and budget tests.
10. Production error redaction tests.
11. CLI worker smoke tests.

Suggested smoke test:

```bash
redis-server --daemonize yes
pytest tests/test_v2_queue.py tests/test_v2_jobs.py tests/test_v2_memory_redis.py
```

---

## 9. Release Checklist

- [ ] Existing public API remains compatible.
- [ ] Queue support is optional dependency.
- [ ] Redis memory is documented as production memory backend.
- [ ] Async workflow mode has reconnectable SSE.
- [ ] `yomai worker` is documented.
- [ ] Job endpoints are production-auth protected.
- [ ] V2 docs include operational guidance.
- [ ] swiftQ integration has a documented minimum supported version.
- [ ] Changelog includes migration notes.
