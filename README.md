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
async def chat(message: str, session_id: str, tone: str = "plain") -> None:
    # Runs before the LLM loop; extra JSON body fields are passed by name.
    pass
```

Run:

```bash
yomai run
```

Open the playground at `http://localhost:8000/__yomai__`.

## Production notes

- Set `YOMAI_ENV=production` to disable the playground.
- Set `YOMAI_API_KEY` to protect agent/workflow routes and production metadata endpoints.
- Routes can override auth with `@app.agent(..., api_key="...")` or `@app.workflow(..., api_key="...")`.
- Session IDs are bearer identifiers; use `SignedSessionMiddleware` or your own auth for public apps.

## Real HTTP/SSE smoke test

```bash
# Reads standard provider env vars.
./scripts/http_sse_smoke.sh
```

Expected SSE events include `tool_start`, `tool_end`, `chunk`, `usage`, and `done`.

## V2 preview: production runtime

Yomai V2 work adds durable async workflow infrastructure while keeping V1 routes compatible.

```python
from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig, RateLimitConfig

app = Yomai(
    llm=LLMConfig(provider="anthropic"),
    memory=MemoryConfig(backend="redis", url="redis://localhost:6379/0"),
    queue=QueueConfig(backend="swiftq", url="redis://localhost:6379/0"),
    rate_limits=RateLimitConfig(requests_per_minute=60, max_concurrent_per_session=3),
)

@app.workflow("/research", mode="async")
async def research(topic: str, runner):
    return {"topic": topic}
```

Async workflow requests return `202 Accepted` with `job_id`, `status_url`, and `stream_url`. Job streams are reconnectable via `Last-Event-ID`.

Useful endpoints:

- `GET /__yomai__/jobs/{job_id}`
- `GET /__yomai__/jobs/{job_id}/stream`
- `POST /__yomai__/jobs/{job_id}/cancel`
- `GET /__yomai__/metrics`

Run a worker for swiftQ-backed workflows:

```bash
yomai worker main:app --concurrency 4
```

Hooks:

```python
@app.on("job.succeeded")
async def on_job_done(event):
    print(event.payload)
```

Manual Redis/swiftQ smoke script:

```bash
uv run python scripts/swiftq_redis_smoke.py worker
uv run python scripts/swiftq_redis_smoke.py web
```

## Testing

```python
from yomai.testing import YomaiTestClient, mock_llm

with mock_llm(["Hello"]):
    client = YomaiTestClient(app)
    text = await client.call("/chat", "Say hello", extra_body={"tone": "friendly"})
```
