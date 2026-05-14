# Agent-to-Agent Routing

Enable multi-agent orchestration where one agent delegates subtasks to other agents as tools.

## Basic Usage

```python
@app.agent("/researcher", tools=[search])
async def researcher(message: str, session_id: str):
    """Deep research agent."""
    pass

@app.agent("/writer", tools=[format_text])
async def writer(message: str, session_id: str):
    """Content writing agent."""
    pass

# Orchestrator that calls both sub-agents
@app.agent("/orchestrator", tools=[
    agent_tool(researcher, name="researcher", description="Research a topic in depth"),
    agent_tool(writer, name="writer", description="Write polished content"),
])
async def orchestrator(message: str, session_id: str):
    pass
```

The LLM can now call `researcher` and `writer` as tools — the framework handles the full agent loop for each sub-agent call, including tool calls, streaming, and memory.

## Auto-Schema Generation

`agent_tool()` automatically generates a tool JSON schema from the agent's function signature:

```python
def my_agent(message: str, topic: str, session_id: str) -> None: ...

tool = agent_tool(my_agent, name="expert")
# tool.schema = {
#   "description": "Delegate a task to the expert agent...",
#   "properties": {
#     "message": {"type": "string", "description": "The message to send"},
#     "topic": {"type": "string", "description": "Parameter: topic"}
#   },
#   "required": ["message"]
# }
```

Parameters named `session_id`, `runner`, or `request` are excluded from the tool schema (they are injected by the framework).

## Agent Registry

Agents decorated with `@app.agent` are automatically registered:

```python
# Access via the registry
tool = app.agents_registry.as_tool("researcher")
print(app.agents_registry.list_agents())  # ['researcher', 'writer', 'orchestrator']
```

## Safety: Cycle Detection

The framework detects circular agent references at registration time:

```python
registry.detect_cycles()
# Returns: [["agent_a", "agent_b", "agent_a"]] if cycle exists
```

## Safety: Depth Limiting

Maximum call depth is enforced (default: 5) to prevent unbounded recursion:

```python
tool = agent_tool(my_agent, name="helper", max_depth=3)
```

If depth is exceeded, an error result is returned to the calling agent instead of crashing.

## Nested Agent Calls in Workflows

```python
@app.workflow("/pipeline")
async def pipeline(topic: str, runner):
    # Run researcher agent as a workflow step
    findings = await runner.step("research", researcher, f"Research: {topic}")

    # Pass findings to writer
    draft = await runner.step("write", writer, findings)

    return draft
```

## Error Handling

```python
from yomai.agents import AgentCallError, CycleDetected, MaxDepthExceeded

try:
    result = await agent_as_tool(message="do something")
except CycleDetected:
    print("Circular agent reference detected")
except MaxDepthExceeded:
    print("Agent call depth limit reached")
```
