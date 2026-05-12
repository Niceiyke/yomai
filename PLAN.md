# Plan: SSE Consolidation, Demo App Update, Module Refactoring

## Phase 1: SSE Consolidation

### Current State
- `yomai/streaming/sse.py` - Core SSE functions (format_sse, sse_chunk, etc.)
- `yomai/workflow/events.py` - Workflow SSE helpers (sse_step_start, sse_result)
- No unified public API

### Changes
1. **Create `yomai/streaming/__init__.py`**
   - Export all SSE functions from `sse.py`
   - Re-export workflow events from `yomai.workflow.events`
   - Add `SSEEvent` type alias for documentation

2. **Update `yomai/streaming/sse.py`**
   - Add missing helpers if any
   - Add type stubs

3. **Update exports in `yomai/__init__.py`**
   - Optionally export SSE utilities

### Files Changed
- `yomai/streaming/__init__.py` (NEW)
- `yomai/streaming/sse.py` (update)
- `yomai/__init__.py` (update)

---

## Phase 2: Demo App Update

### Current State
- Research agent with Wikipedia/DDG search ✓
- Session management (GET/DELETE) ✓
- Versioned route group ✓
- Missing async workflows, job management, hooks

### Changes

#### 2.1 Add Async Workflow
```python
@app.workflow("/batch-research", mode="async")
async def batch_research(topics: list[str], runner):
    results = []
    for topic in topics:
        await runner.step("search", research_agent, {"query": topic})
        results.append(runner.last_reply)
    return {"count": len(results), "results": results}
```

#### 2.2 Add Hooks
```python
@app.on("job.succeeded")
async def on_job_done(event):
    print(f"Job {event.payload['job_id']} completed")
```

#### 2.3 Add Rate Limiting Config
```python
app = Yomai(
    llm=LLMConfig(max_tokens=512),
    rate_limits=RateLimitConfig(requests_per_minute=30),
)
```

#### 2.4 Add Job Status Endpoint
```python
@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict:
    job = await app.jobs.get(job_id)
    return job.to_dict() if job else {"error": "not found"}
```

#### 2.5 Add Tests
- `tests/test_research_agent.py` - Test agent with mock LLM
- `tests/test_workflows.py` - Test async workflow
- `tests/test_sessions.py` - Test session management

### Files Changed
- `demo-app/app/main.py` - Add workflows
- `demo-app/app/agents/researcher.py` - Add hooks, rate limits
- `demo-app/tests/` - New test files

---

## Phase 3: Module Refactoring

### Current Issues
- `yomai/core/app.py` - 1224 lines (too large)
- `yomai/core/router.py` - 960 lines (too large)

### New Structure

#### `yomai/core/`
```
core/
├── __init__.py
├── app.py          # Main Yomai class (keep ~400 lines)
├── auth.py         # API key validation, session auth
├── routes.py       # Route registration, metadata
├── jobs.py         # Job status/stream endpoints
├── router.py       # AgentRoute, WorkflowRoute handlers
└── params.py       # Parameter coercion, type hints
```

### Details

#### 3.1 Extract to `auth.py`
- `verify_api_key()` function
- `verify_bearer()` helper
- `check_rate_limit()` if applicable

#### 3.2 Extract to `routes.py`
- `_validate_new_path()`
- `_extract_path_params()`
- `_route_params()`
- Route metadata builders

#### 3.3 Extract to `jobs.py`
- `_job_status()`
- `_job_stream()`
- `_job_cancel()`
- `_metrics()`

#### 3.4 Extract to `params.py`
- `_handler_type_hints()`
- `_get_annotation()`
- `_coerce_value()`
- All type coercion logic from router.py

#### 3.5 Keep in `app.py`
- `Yomai` class main logic
- `Depends`, `RouteGroup` classes
- `@app.agent()`, `@app.workflow()` decorators
- Provider factory, memory initialization
- Queue backend logic

#### 3.6 Keep in `router.py`
- `AgentRoute.handle()` - core agent logic
- `WorkflowRoute` - workflow handler
- `GetRoute`, `DeleteRoute`, etc.
- SSE streaming helpers

### Files Changed
- `yomai/core/auth.py` (NEW)
- `yomai/core/routes.py` (NEW)
- `yomai/core/jobs.py` (NEW)
- `yomai/core/params.py` (NEW)
- `yomai/core/app.py` (refactored)
- `yomai/core/router.py` (refactored)

---

## Execution Order

1. **SSE Consolidation** (small, safe)
   - Create `__init__.py`
   - Verify all tests pass

2. **Demo App Updates** (medium, user-facing)
   - Add workflows
   - Add tests
   - Test locally

3. **Module Refactoring** (larger, needs care)
   - Extract files one by one
   - Run tests after each extraction
   - Update imports in `__init__.py`

---

## Success Criteria

- ✅ All existing tests pass
- ✅ pyright reports 0 errors
- ✅ Demo app runs with all features
- ✅ SSE utilities are documented and accessible