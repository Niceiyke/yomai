from __future__ import annotations

"""Manual swiftQ + Redis smoke test for Yomai V2.

Usage:
  1. Start Redis: docker compose up -d redis  # or redis-server
  2. Terminal A: uv run python scripts/swiftq_redis_smoke.py worker
  3. Terminal B: uv run python scripts/swiftq_redis_smoke.py web
  4. Terminal C: curl -X POST http://localhost:8000/research -H 'content-type: application/json' -d '{"topic":"ai"}'
     Then curl the returned stream_url.
"""

import sys

import uvicorn

from yomai import Yomai
from yomai.config import LLMConfig, MemoryConfig, QueueConfig
from yomai.workflow import WorkflowRunner

app = Yomai(
    llm=LLMConfig(api_key=""),
    memory=MemoryConfig(backend="redis", url="redis://localhost:6379/0"),
    queue=QueueConfig(backend="swiftq", url="redis://localhost:6379/0"),
)


@app.workflow("/research", mode="async")
async def research(topic: str, runner: WorkflowRunner) -> dict[str, str]:
    await runner.raise_if_cancelled()
    return {"topic": topic, "summary": f"smoke result for {topic}"}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "web"
    if mode == "worker":
        backend = app._get_queue_backend()
        backend.work(concurrency=1)
    elif mode == "web":
        uvicorn.run(app, host="127.0.0.1", port=8000)
    else:
        raise SystemExit("usage: swiftq_redis_smoke.py [web|worker]")


if __name__ == "__main__":
    main()
