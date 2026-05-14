"""API key authentication backend."""

from __future__ import annotations

import hmac

from yomai._types import Request
from yomai.auth import AuthBackend, AuthResult


class APIKeyAuth(AuthBackend):
    """Validate requests using a static set of API keys.

    Keys are checked via constant-time comparison (hmac.compare_digest).

    Args:
        keys: A set of valid API key strings.
        header: The HTTP header to read the key from (default: ``Authorization``).
        prefix: Expected prefix before the key (default: ``Bearer ``).
    """

    def __init__(
        self,
        keys: set[str] | None = None,
        *,
        header: str = "Authorization",
        prefix: str = "Bearer ",
    ) -> None:
        self._keys = keys or set()
        self._header = header.lower()
        self._prefix = prefix

    async def authenticate(self, request: Request) -> AuthResult | None:
        if not self._keys:
            return None

        auth_value = request.headers.get(self._header, "")
        for key in self._keys:
            expected = f"{self._prefix}{key}"
            if hmac.compare_digest(auth_value, expected):
                return AuthResult(identity=key[:12] + "..." if len(key) > 12 else key)
        return None
