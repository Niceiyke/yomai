# Yomai Tutorial — Build an Agent from Scratch

This guide walks you through building a Yomai application step by step.
You'll start with a simple weather agent and build up to the full support agent.

## Prerequisites

- Python 3.10+
- An LLM API key (Anthropic, OpenAI, or Ollama)

```bash
pip install yomai anthropic uvicorn
# or: uv add yomai anthropic uvicorn
```

## Step 1 — Your First Agent

Create `main.py`:

```python
from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"

@app.agent("/chat", tools=[get_weather])
async def chat(message: str):
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

Start the server:

```bash
ANTHROPIC_API_KEY=sk-... python main.py
# or: ANTHROPIC_API_KEY=sk-... uvicorn main:app --reload
```

Test it:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the weather in Tokyo?"}'
```

You'll see streaming SSE events: the LLM calls `get_weather("Tokyo")`, gets the result,
and streams back a natural language response.

**What's happening:**
- `@tool` registers `get_weather` and generates its JSON schema from type hints
- `@app.agent("/chat", tools=[get_weather])` creates a POST endpoint that streams SSE
- The agent handler runs before the LLM loop (can validate inputs, load state)
- Yomai manages the tool execution loop: LLM → tool call → result → LLM → response

## Step 2 — Add Memory for Session Persistence

Yomai supports pluggable memory backends. Let's use SQLite:

```python
from yomai.config import MemoryConfig

app = Yomai(
    llm=LLMConfig(provider="anthropic"),
    memory=MemoryConfig(backend="sqlite", db_path="chat_history.db", max_messages=30),
)
```

Now the agent remembers conversations across requests as long as the client sends
the same `X-Session-Id` header.

```bash
# First message
curl -X POST http://localhost:8000/chat \
  -H "X-Session-Id: user-123" \
  -H "Content-Type: application/json" \
  -d '{"message": "My name is Alice"}'

# Follow-up — the agent remembers Alice
curl -X POST http://localhost:8000/chat \
  -H "X-Session-Id: user-123" \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my name?"}'
```

Available backends: `"dict"` (in-memory, tests), `"sqlite"` (persistent), `"redis"` (scalable).

## Step 3 — Add Type-Safe Extra Parameters

Agent handlers can accept typed JSON body fields:

```python
from uuid import UUID

@app.agent("/chat", tools=[get_weather])
async def chat(message: str, user_id: UUID, language: str = "en"):
    print(f"User {user_id} speaks {language}")
    # Run your validation/auth logic before the LLM

# Client sends extra fields in the JSON body:
# {"message": "hi", "user_id": "550e8400-...", "language": "fr"}
```

Yomai automatically coerces JSON values to the annotated Python types (UUID, datetime,
enums, Pydantic models, Literals, generics).

## Step 4 — Rate Limiting

```python
from yomai.config import RateLimitConfig

app = Yomai(
    llm=LLMConfig(provider="anthropic"),
    memory=MemoryConfig(backend="sqlite"),
    rate_limits=RateLimitConfig(requests_per_minute=20, max_concurrent_per_session=3),
)
```

Rate-limited requests get a 429 response with `retry_after` in the body.

## Step 5 — Workflows (Multi-Step Automation)

Workflows chain multiple agents or tool calls:

```python
@app.workflow("/auto-reply", mode="async")
async def auto_reply(message: str, session_id: str, runner):
    # Step 1: Analyze sentiment
    sentiment = await runner.step(
        "Analyze sentiment",
        sentiment_agent,
        message,
    )

    # Step 2: Draft reply
    if sentiment == "urgent":
        reply = await runner.step(
            "Draft urgent reply",
            urgent_reply_agent,
            message,
        )
    else:
        reply = f"Thanks for your message. We'll get back to you soon."

    return {"sentiment": sentiment, "reply": reply}
```

Async workflows return a `job_id` immediately (202 Accepted), then the client polls
`/__yomai__/jobs/{job_id}` or streams `/__yomai__/jobs/{job_id}/stream` for progress.

## Step 6 — REST Endpoints

Add plain GET/POST/PUT/DELETE endpoints alongside agents:

```python
@app.get("/history/{session_id}")
async def get_history(session_id: str):
    from yomai.memory import SqliteMemory
    mem = SqliteMemory(db_path="chat_history.db")
    return {"messages": await mem.load(session_id)}

@app.delete("/history/{session_id}")
async def clear_history(session_id: str):
    from yomai.memory import SqliteMemory
    mem = SqliteMemory(db_path="chat_history.db")
    await mem.clear(session_id)
    return {"status": "cleared"}
```

## Step 7 — Hooks (Lifecycle Events)

```python
@app.on("job.succeeded")
async def on_job_done(event):
    print(f"Job {event.payload['job_id']} completed!")
    # Send a Slack notification, update a database, etc.

@app.on("error")
async def on_error(event):
    print(f"Error in {event.payload['route']}: {event.payload['error']}")
```

Available hooks: `job.queued`, `job.started`, `job.succeeded`, `job.failed`,
`job.cancelled`, `workflow.start`, `workflow.done`, `workflow.failed`, `error`.

## Step 8 — Authentication

```python
from yomai.auth import APIKeyAuth

app = Yomai(
    llm=LLMConfig(provider="anthropic"),
    auth=APIKeyAuth(keys={"sk-prod-123", "sk-prod-456"}, header="X-API-Key", prefix=""),
)
```

All routes now require `X-API-Key: sk-prod-123`. Per-route override:

```python
@app.agent("/public-chat", auth=NoAuth())
async def public_chat(message: str):
    pass
```

JWT auth is available via `JWTAuth(secret="...")` (requires `pip install pyjwt`).

## Step 9 — Observability

```bash
# Structured JSON logging
YOMAI_LOG_FORMAT=json YOMAI_LOG_LEVEL=DEBUG uvicorn main:app

# Prometheus metrics (requires prometheus-client)
pip install prometheus-client
curl http://localhost:8000/__yomai__/metrics

# Deep health check
curl "http://localhost:8000/__yomai__/health?depth=deep"
```

## Step 10 — Deploy

```bash
# Production server
yomai serve main:app --workers 4 --proxy-headers

# Docker
docker compose up -d

# Check the deployment guide
cat docs/deployment.md
```

## Next Steps

- **Support Agent demo**: A full production app at `examples/support_agent/`
- **API Reference**: `docs/api-reference.md` — every class, function, and config option
- **Production Plan**: `PRODUCTION_PLAN.md` — the roadmap from v0.1.0 to v1.0.0
- **Playground**: Open `http://localhost:8000/__yomai__` in development mode
