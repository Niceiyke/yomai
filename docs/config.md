# Config

Yomai config is passed to `Yomai(...)`.

```python
from yomai import Yomai
from yomai.config import LLMConfig

app = Yomai(
    llm=LLMConfig(
        provider="anthropic", # or "openai"
        model="claude-sonnet-4-20250514",
        api_key="...",
        base_url="https://api.example.com/anthropic",  # optional custom gateway
    )
)
```

Environment variables supported:

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `YOMAI_PROVIDER`
- `YOMAI_MODEL`
- `YOMAI_ENV=production` disables the playground.

Some reasoning models stream `<think>...</think>` blocks as normal text. Yomai does not strip provider output by default.
