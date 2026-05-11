# Workflows

Workflows are Python functions that sequence agent steps.

```python
from yomai.workflow import WorkflowRunner

@app.workflow("/research")
async def research(topic: str, runner: WorkflowRunner):
    summary = await runner.step("summarize", summarize_agent, topic)
    return summary
```

Workflow endpoints stream:

- `step_start`
- agent events
- `step_done`
- `result`
- `done`
