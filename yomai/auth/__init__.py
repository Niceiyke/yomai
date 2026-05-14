"""Authentication backends for Yomai.

Built-in backends:
    APIKeyAuth   — static API key validation
    JWTAuth      — JWT token validation (requires PyJWT)

Custom backends implement the ``AuthBackend`` protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from yomai._types import Request


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Result of a successful authentication."""

    identity: str  # e.g. user id, API key name, or subject claim
    scopes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return True


class AuthBackend(ABC):
    """Protocol for Yomai authentication backends.

    Implementations must be stateless callables that return ``AuthResult``
    on success or ``None`` on failure.
    """

    @abstractmethod
    async def authenticate(self, request: Request) -> AuthResult | None:
        """Return AuthResult if the request is authenticated, else None."""
        ...


class NoAuth(AuthBackend):
    """Allow all requests without authentication (default)."""

    async def authenticate(self, request: Request) -> AuthResult | None:
        return AuthResult(identity="anonymous")
