"""Structured, PII-minimizing request observability."""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("hmlet.api")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def safe_identifier(value: str | None) -> str | None:
    if value and _SAFE_ID.fullmatch(value):
        return value
    return None


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        supplied = safe_identifier(request.headers.get("x-request-id"))
        request_id = supplied or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            # Never log request/response bodies, Authorization headers, or query strings.
            logger.info(json.dumps({
                "event": "http_request",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }, separators=(",", ":")))
