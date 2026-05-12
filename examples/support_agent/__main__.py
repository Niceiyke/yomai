"""Entry point: uv run python -m examples.support_agent"""
from __future__ import annotations

import uvicorn

from examples.support_agent.main import app

uvicorn.run(app, host="0.0.0.0", port=8000)
