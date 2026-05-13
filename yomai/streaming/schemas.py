"""Pydantic models for all SSE event data structures.

These models are used by the SSE helpers in ``streaming/sse.py`` to ensure
field-level type safety when building event payloads. Each event type is a
separate model; the SSE event name (wire-level ``event:`` field) serves as
the discriminator.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Agent events
# ---------------------------------------------------------------------------

class ChunkData(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str


class ToolStartData(BaseModel):
    type: Literal["tool_start"] = "tool_start"
    name: str
    args: dict[str, Any]
    id: str


class ToolEndData(BaseModel):
    type: Literal["tool_end"] = "tool_end"
    id: str
    result: str
    duration_ms: int


class UsageData(BaseModel):
    type: Literal["usage"] = "usage"
    input_tokens: int
    output_tokens: int
    cost_usd: float


class DoneData(BaseModel):
    type: Literal["done"] = "done"


class ErrorData(BaseModel):
    type: Literal["error"] = "error"
    message: str
    code: str = "error"


# ---------------------------------------------------------------------------
# Workflow events
# ---------------------------------------------------------------------------

class StepStartData(BaseModel):
    type: Literal["step_start"] = "step_start"
    name: str
    index: int
    of: int | None = None


class StepDoneData(BaseModel):
    type: Literal["step_done"] = "step_done"
    name: str
    duration_ms: int


class ResultData(BaseModel):
    type: Literal["result"] = "result"
    content: str


# ---------------------------------------------------------------------------
# Infrastructure events
# ---------------------------------------------------------------------------

class PingData(BaseModel):
    type: Literal["ping"] = "ping"


class InterruptData(BaseModel):
    type: Literal["interrupt"] = "interrupt"
    id: str
    message: str


# ---------------------------------------------------------------------------
# Graph events (action-discriminated, event: graph)
# ---------------------------------------------------------------------------

class GraphUpsertData(BaseModel):
    action: Literal["upsert"] = "upsert"
    id: str
    label: str
    kind: str
    status: str = "running"
    parent: str | None = None
    meta: dict[str, Any] | None = None


class GraphEdgeData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    action: Literal["edge"] = "edge"
    from_: str = Field(..., alias="from")
    to: str
    label: str = ""


class GraphUpdateData(BaseModel):
    action: Literal["update"] = "update"
    id: str
    status: str
    meta: dict[str, Any] | None = None


class GraphClearData(BaseModel):
    action: Literal["clear"] = "clear"


# ---------------------------------------------------------------------------
# Union type for parsing / consumer side
# ---------------------------------------------------------------------------

SSEEventData = (
    ChunkData
    | ToolStartData
    | ToolEndData
    | UsageData
    | DoneData
    | ErrorData
    | StepStartData
    | StepDoneData
    | ResultData
    | PingData
    | InterruptData
    | GraphUpsertData
    | GraphEdgeData
    | GraphUpdateData
    | GraphClearData
)
