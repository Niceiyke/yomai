# Tools

Decorate sync or async functions with `@tool` and pass them explicitly to an agent.

```python
from typing import Literal
from yomai import tool

@tool
def get_weather(city: str, units: Literal["f", "c"] = "f") -> str:
    """Get current weather for a city."""
    return f"72°{units.upper()} and sunny in {city}"
```

Yomai derives JSON schema from type hints and emits:

- `tool_start`
- `tool_end`

Supported schema hints include basic scalar types, lists, dictionaries, optional values, and simple `Literal[...]` enums.

Tool execution safety:

- Only tools passed to the current `@app.agent(..., tools=[...])` can be executed by that route.
- Tool arguments are checked against the Python function signature before execution.
- Basic runtime type checks are applied for `str`, `int`, `float`, `bool`, `list`, and `dict` annotations.

Tool execution is sequential in V1.
