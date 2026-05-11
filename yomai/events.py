from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgentStartEvent:
    session_id: str
    message: str
    path: str


@dataclass(slots=True)
class AgentDoneEvent:
    session_id: str
    reply: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int


@dataclass(slots=True)
class ToolEndEvent:
    tool_name: str
    args: dict
    result: str
    duration_ms: int


@dataclass(slots=True)
class ErrorEvent:
    error: Exception
    session_id: str
    path: str
