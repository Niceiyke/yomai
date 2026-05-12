# Config

Yomai config is passed to `Yomai(...)`.

```python
from yomai import Yomai
from yomai.config import DevConfig, LLMConfig, MemoryConfig

app = Yomai(
    llm=LLMConfig(
        provider="anthropic",  # or "openai"
        model="claude-sonnet-4-20250514",
        api_key="...",
        base_url="https://api.example.com/anthropic",  # optional custom gateway
    ),
    memory=MemoryConfig(backend="sqlite", ttl_hours=24, max_messages=20),
    dev=DevConfig(api_key="optional-yomai-api-key"),
)
```

Provider-specific defaults:

- `provider="anthropic"` defaults to `claude-sonnet-4-20250514` and reads `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`.
- `provider="openai"` defaults to `gpt-4o-mini` and reads `OPENAI_API_KEY` / `OPENAI_BASE_URL`.

Environment variables supported:

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `YOMAI_API_KEY`
- `YOMAI_APP_TITLE`
- `YOMAI_ENV=production`

Production behavior:

- `YOMAI_ENV=production` disables the playground.
- In production, metadata endpoints such as `/__yomai__/routes` and `/__yomai__/openapi.json` require `Authorization: Bearer <YOMAI_API_KEY>` when an API key is configured.
- Streaming route errors return a generic client message in production.
- Agent and workflow routes can override global auth with `api_key="..."`; use `api_key=""` to make a specific route public.

See `docs/security.md` for signed session middleware and public deployment guidance.

Some reasoning models stream `<think>...</think>` blocks as normal text. Yomai does not strip provider output by default. Enable with `LLMConfig(strip_reasoning=True)`.
