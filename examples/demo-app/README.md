# Research Assistant — powered by Yomai

A multi-tool LLM agent that searches the web, fetches URLs, and summarizes content.
Built as a demonstration of [Yomai](https://github.com/your-org/yomai), a Python framework for streaming LLM agents.

## Features

- **`/research`** — agent endpoint with 4 tools:
  - `web_search` — DuckDuckGo JSON API search
  - `fetch_url` — raw HTML fetch with tag stripping
  - `summarize_text` — extractive summary
  - `convert_units` — km↔mi, kg↔lb, °C↔°F, etc.

## Quick start

```bash
# Install
cd demo-app
uv sync

# Run
export ANTHROPIC_API_KEY=sk-...     # or OPENAI_API_KEY
export ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
export YOMAI_MODEL=MiniMax-M2.7
uv run python -m app.main

# In another terminal — smoke test
./scripts/smoke.sh
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/research` | Ask a research question (streaming SSE) |
| GET | `/__yomai__/health` | Health check |
| GET | `/__yomai__/routes` | Route metadata |
| GET | `/__yomai__/openapi.json` | OpenAPI 3.1 schema |
| GET | `/__yomai__` | Interactive playground UI |

## Request / response

```bash
curl -s -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"query": "What is quantum computing?", "session_id": "my-session"}'
```

SSE events: `tool_start`, `tool_end`, `chunk`, `usage`, `done`.

## Tests

```bash
uv run pytest
uv run pyright
```