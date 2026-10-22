"""core/logging.py — Structured JSON logging, one format everywhere.

Call configure_logging() once at app startup.  After that, every
structlog.get_logger() call shares the same processor chain and every
log line is a JSON object with at least: timestamp, level, logger, event,
and request_id (when set by RequestIdMiddleware).

JSON is the production default.  Set json_output=False for development
console output (coloured, human-readable).
"""

from __future__ import annotations

import logging
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


def configure_logging(
    level: str = "INFO",
    json_output: bool = True,
) -> None:
    """Configure structlog and stdlib logging.  Call exactly once at startup."""
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        force=True,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a short request_id into structlog's context for every request.

    The id is also echoed back in the X-Request-Id response header, so that
    support calls can be linked to a specific log line.
    """

    async def dispatch(self, request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=rid)
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response
