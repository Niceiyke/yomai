"""JWT authentication backend (optional — requires PyJWT)."""

from __future__ import annotations

from yomai._types import Request
from yomai.auth import AuthBackend, AuthResult


class JWTAuth(AuthBackend):
    """Validate JWT bearer tokens.

    Requires ``PyJWT`` to be installed: ``pip install pyjwt``.

    Args:
        secret: HMAC secret or RSA public key for verification.
        algorithms: Accepted JWT algorithms (default: ``["HS256"]``).
        audience: Expected ``aud`` claim value.
        issuer: Expected ``iss`` claim value.
        header: HTTP header to read the token from (default: ``Authorization``).
        prefix: Expected prefix before the token (default: ``Bearer ``).
        subject_claim: Claim to use as the identity string (default: ``sub``).
    """

    def __init__(
        self,
        secret: str,
        *,
        algorithms: list[str] | None = None,
        audience: str | None = None,
        issuer: str | None = None,
        header: str = "Authorization",
        prefix: str = "Bearer ",
        subject_claim: str = "sub",
    ) -> None:
        self._secret = secret
        self._algorithms = algorithms or ["HS256"]
        self._audience = audience
        self._issuer = issuer
        self._header = header.lower()
        self._prefix = prefix
        self._subject_claim = subject_claim

    async def authenticate(self, request: Request) -> AuthResult | None:
        try:
            import jwt
        except ImportError:
            return None

        auth_value = request.headers.get(self._header, "")
        if not auth_value.startswith(self._prefix):
            return None

        token = auth_value[len(self._prefix) :]
        try:
            options: dict[str, object] = {"verify_exp": True}
            payload: dict = jwt.decode(  # type: ignore[reportAttributeAccessIssue]
                token,
                self._secret,
                algorithms=self._algorithms,
                audience=self._audience,
                issuer=self._issuer,
                options=options,
            )
        except Exception:
            return None

        identity = str(payload.get(self._subject_claim, "unknown"))
        scopes = payload.get("scopes", [])
        if isinstance(scopes, str):
            scopes = scopes.split()
        return AuthResult(
            identity=identity,
            scopes=list(scopes) if isinstance(scopes, list) else [],
        )
