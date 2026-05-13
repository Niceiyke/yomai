"""Pydantic models for HTTP request/response bodies and route metadata."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRequest(BaseModel):
    """Request body for agent endpoints (``POST`` with SSE streaming).

    ``message`` can be a plain string or a content array for multi-modal
    requests (images, audio)::

        {"message": "What is in this image?"}
        {"message": [{"type": "text", "text": "Describe"}, {"type": "image_url", "image_url": {"url": "..."}}]}

    Additional fields defined in the handler function signature are accepted
    via ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    message: str | list[dict[str, Any]] = Field(
        ..., min_length=1, description="User message (string or multi-modal content array)"
    )

    @property
    def message_text(self) -> str:
        """Extract a plain-text preview for use in hooks, graphs, and memory."""
        if isinstance(self.message, str):
            return self.message
        for block in self.message:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))
        return "[multi-modal]"


# ---------------------------------------------------------------------------
# Route metadata
# ---------------------------------------------------------------------------


class RouteParam(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    type: str = "string"
    required: bool = True
    default: Any = None
    in_: str = Field(default="body", alias="in")


RouteType = Literal["agent", "workflow_stream", "workflow_async", "get", "delete", "put", "patch", "head", "options"]


class RouteMeta(BaseModel):
    """Metadata for a registered route, consumed by playground UI and OpenAPI generator."""

    path: str
    type: str  # RouteType — kept as str for extensibility
    tools: list[str] = Field(default_factory=list)
    params: list[RouteParam] = Field(default_factory=list)
    body_params: list[str] = Field(default_factory=list)
    path_params: list[str] = Field(default_factory=list)
    injected_params: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    description: str | None = None
    deprecated: bool = False
    system: str | None = None
    mode: str | None = None
    cors: dict[str, Any] | None = None
