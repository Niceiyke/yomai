# Release Checklist

Before publishing:

```bash
uv sync
uv run pyright
uv run pytest -q
./scripts/http_sse_smoke.sh
YOMAI_PROVIDER=openai ./scripts/http_sse_smoke.sh
./scripts/install_smoke.sh
uv build
```

Manual checks:

- Open `http://localhost:8000/__yomai__` and verify streaming/tool cards.
- Verify `llm.md`, `.env`, and secrets are not committed.
- Rotate any key that has appeared in logs, screenshots, or chat transcripts.
- Review `CHANGELOG.md`.
