"""Structured logging configuration for crypto-server."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured JSON logging."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a configured structured logger."""
    return structlog.get_logger(name)


def log_error(logger: Any, error: Exception, context: dict[str, Any] | None = None) -> None:
    """Log an exception with sanitized context."""
    payload: dict[str, Any] = {
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    if context:
        payload.update(_sanitize(context))
    logger.error("error_occurred", **payload, exc_info=True)


def _sanitize(values: dict[str, Any]) -> dict[str, Any]:
    sensitive = {"api_key", "apikey", "secret", "password", "token", "authorization", "auth"}
    return {k: ("***REDACTED***" if k.lower() in sensitive else v) for k, v in values.items()}
