"""Structured JSON logging for Yomai.

Controlled by environment variables:
    YOMAI_LOG_LEVEL  — default "INFO" ("DEBUG", "INFO", "WARNING", "ERROR")
    YOMAI_LOG_FORMAT — "json" (default) or "console" (human-readable)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

LOG_LEVEL = os.environ.get("YOMAI_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("YOMAI_LOG_FORMAT", "json").lower()


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])
        # Include any extra fields passed via `extra=` kwarg
        for key in (
            "route",
            "session_id",
            "method",
            "status_code",
            "duration_ms",
            "tokens_in",
            "tokens_out",
            "tool_name",
            "job_id",
            "error",
        ):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        return json.dumps(payload, separators=(",", ":"))


def setup() -> None:
    """Configure Yomai root logger once at startup."""
    root = logging.getLogger("yomai")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    if LOG_FORMAT == "json":
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)-7s %(name)s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    root.addHandler(handler)
    root.propagate = False


def get(name: str) -> logging.Logger:
    """Get a logger for a Yomai subsystem (e.g. 'yomai.agent', 'yomai.llm')."""
    return logging.getLogger(f"yomai.{name}")
