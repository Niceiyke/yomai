from __future__ import annotations

import asyncio
import os
import traceback
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class ErrorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Any) -> Response:
        try:
            return await call_next(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if os.environ.get("YOMAI_ENV") != "production":
                traceback.print_exc()
                message = str(exc)
            else:
                message = "Internal server error"
            return JSONResponse({"type": "error", "message": message, "code": exc.__class__.__name__}, status_code=500)
