# Yomai

Streaming-first Python framework for serving LLM agents over HTTP.

```python
from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))

@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"

@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str) -> None:
    pass
```

Run:

```bash
yomai run
```

Open the playground at `http://localhost:8000/__yomai__`.

## Real HTTP/SSE smoke test

```bash
# Reads standard provider env vars.
./scripts/http_sse_smoke.sh
```

Expected SSE events include `tool_start`, `tool_end`, `chunk`, `usage`, and `done`.

## Testing

```python
from yomai.testing import YomaiTestClient, mock_llm

with mock_llm(["Hello"]):
    client = YomaiTestClient(app)
    text = await client.call("/chat", "Say hello")
```
