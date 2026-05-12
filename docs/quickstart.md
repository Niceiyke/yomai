# Quickstart

```bash
uv add yomai uvicorn anthropic
```

```python
from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))

@tool
async def get_weather(city: str) -> str:
    return f"72°F and sunny in {city}"

@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str, tone: str = "plain") -> None:
    # The handler runs before the LLM loop. Use it for validation,
    # request setup, logging, or reading extra body fields.
    pass
```

Run:

```bash
uvicorn main:app --reload
```

Send:

```bash
curl -N -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: demo-session' \
  -d '{"message":"Use the weather tool for Tokyo","tone":"friendly"}'
```

Agent request bodies must include `message`. Any other JSON fields are passed to matching parameters on the decorated agent function and validated/coerced from type hints. `session_id` is injected from `X-Session-Id` or auto-generated.
