# Tools

Decorate sync or async functions with `@tool` and pass them explicitly to an agent.

```python
from yomai import tool

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"
```

Yomai derives JSON schema from type hints and emits:

- `tool_start`
- `tool_end`

Tool execution is sequential in V1.
