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
async def chat(message: str, session_id: str) -> None:
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
  -d '{"message":"Use the weather tool for Tokyo"}'
```
