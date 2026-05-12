from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SignedSessionMiddleware(BaseHTTPMiddleware):
    """Require X-Session-Id to be signed as `session_id.signature`.

    This middleware is optional. It is useful when exposing Yomai publicly because
    raw session IDs are bearer identifiers.
    """

    def __init__(self, app: Any, secret: str, header_name: str = "X-Session-Id") -> None:
        super().__init__(app)
        self.secret = secret.encode()
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        value = request.headers.get(self.header_name)
        if value:
            session_id = self.verify(value)
            if session_id is None:
                return JSONResponse({"error": "Invalid session signature"}, status_code=401)
            request.scope["headers"] = [
                (k, session_id.encode() if k.decode().lower() == self.header_name.lower() else v)
                for k, v in request.scope["headers"]
            ]
        return await call_next(request)

    def sign(self, session_id: str) -> str:
        digest = hmac.new(self.secret, session_id.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return f"{session_id}.{signature}"

    def verify(self, signed_session_id: str) -> str | None:
        session_id, sep, signature = signed_session_id.rpartition(".")
        if not sep or not session_id or not signature:
            return None
        expected = self.sign(session_id).rpartition(".")[2]
        if not hmac.compare_digest(signature, expected):
            return None
        return session_id
