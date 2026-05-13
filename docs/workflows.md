# Workflows

Workflows are Python functions that orchestrate agent calls, tools, branches, and
delegation. Use `@app.workflow(path)` to register a streaming workflow endpoint.

## Quick start

```python
from yomai.workflow import WorkflowRunner

@app.workflow("/research")
async def research(topic: str, runner: WorkflowRunner):
    summary = await runner.step("summarize", summarize_agent, topic)
    return summary
```

The `runner` parameter is injected automatically (like `session_id` or `request`).

## Shared state

`runner.state` is a dict that accumulates step outputs keyed by step name:

```python
async def pipeline(runner: WorkflowRunner):
    await runner.step("outline", outline_agent, "plan the article")
    # runner.state["outline"] == agent's last reply

    await runner.step("draft", draft_agent, runner.state["outline"])
    # runner.state["draft"] == agent's reply

    return runner.state  # returns {"outline": "...", "draft": "..."}
```

## Retry

Steps can retry on failure with exponential backoff:

```python
# Retry up to 3 times, starting at 1s backoff (1s, 2s, 4s...)
result = await runner.step("flaky", agent, input, retries=3, backoff_secs=1.0)
```

## Direct tool execution

Call `@tool`-decorated functions directly — no LLM overhead:

```python
@tool
def fetch_document(url: str) -> str:
    return httpx.get(url).text

async def pipeline(runner: WorkflowRunner):
    doc = await runner.tool(fetch_document, url="https://example.com")
    return await runner.step("analyze", analyst, doc)
```

Useful for: API calls, database writes, file I/O, any deterministic operation.

## Branching

Conditionally route between different agents based on state:

```python
async def pipeline(query: str, runner: WorkflowRunner):
    await runner.step("classify", classifier, query)

    return await runner.branch(
        "route",
        condition=lambda s: s.get("classify", "").startswith("billing"),
        on_true=lambda: runner.step("billing", billing_agent, query),
        on_false=lambda: runner.step("general", general_agent, query),
    )
```

Branches appear in the graph visualizer with the taken path labeled.

## Parallel execution

Run steps concurrently (e.g., fan-out to multiple specialists):

```python
results = await runner.parallel([
    runner.step("section_1", writer, "introduction"),
    runner.step("section_2", writer, "body"),
    runner.step("section_3", writer, "conclusion"),
])
```

## Agent delegation

One agent can dynamically call another agent — the orchestrator LLM
decides when and which specialist to delegate to:

```python
@app.agent("/orchestrator")
async def orchestrator(message: str, session_id: str) -> None:
    pass  # LLM decides which tool to invoke

@app.workflow("/delegation-example")
async def delegation(runner: WorkflowRunner):
    # Orchestrator agent can call the tool ↓ dynamically
    answer = await runner.delegate(orchestrator, "classify and route: " + query)
    return answer
```

Combine with `@tool` to expose delegation as a tool to the orchestrator:

```python
@tool
async def call_billing(query: str) -> str:
    """Ask the billing specialist about invoices or payments."""
    return await runner.delegate(billing_agent, query)

@app.agent("/triage", tools=[call_billing, call_technical])
async def triage(message: str, session_id: str) -> None:
    pass  # LLM decides when to call call_billing or call_technical
```

## Cancellation

Long-running workflows can be cancelled via the jobs API. Check for
cancellation between steps with `runner.raise_if_cancelled()` (called
automatically) or `await runner.cancelled()`.

## SSE Events

Workflow endpoints stream:

- `step_start` / `step_done` — per step
- `graph` — DAG nodes and edges for the playground visualizer
- Agent events (`chunk`, `tool_start`, `tool_end`, `usage`) — passed through from each step
- `result` — final workflow output
- `done` / `error` — completion

## API Reference

### `WorkflowRunner`

| Method | Description |
|--------|-------------|
| `await step(name, agent_fn, input, *, retries=0, backoff_secs=1.0) → str` | Run an agent. Stores output in `state[name]`. |
| `await parallel(steps) → list` | Run agent steps concurrently. |
| `await branch(name, *, condition, on_true, on_false) → Any` | Evaluate `condition(state)` and execute one branch. |
| `await tool(fn, /, **kwargs) → Any` | Call a `@tool` function directly (no LLM). |
| `await delegate(agent_fn, prompt, *, system="", tools=None) → str` | Run an agent as a sub-call. Stores output in `state[agent_name]`. |
| `await cancelled() → bool` | Check if the job has been cancelled. |
| `await raise_if_cancelled()` | Raise `CancelledError` if cancelled. |
| `.state: dict` | Shared state dict. Auto-populated from step/delegate outputs. |
| `.session_id: str` | Current session ID. |
