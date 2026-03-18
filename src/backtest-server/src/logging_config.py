"""Structured logging configuration for backtest-server."""

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured logging with structlog."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Suppress httpx/httpcore request logging — it leaks full URLs with API keys
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
    """Get a configured logger instance."""
    return structlog.get_logger(name)


def log_api_call(
    logger: Any,
    service: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Log an external API call."""
    sanitized_params = _sanitize_params(params or {})
    logger.info(
        "api_call",
        service=service,
        endpoint=endpoint,
        params=sanitized_params,
    )


def log_error(
    logger: Any,
    error: Exception,
    context: dict[str, Any] | None = None,
) -> None:
    """Log an error with full context."""
    error_context: dict[str, Any] = {
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    if context:
        for key, value in context.items():
            if key == "event":
                error_context["error_event"] = value
            else:
                error_context[key] = value

    logger.error("error_occurred", **error_context, exc_info=True)


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Sanitize parameters to remove sensitive data."""
    sensitive_keys = {
        "api_key",
        "apikey",
        "secret",
        "password",
        "token",
        "authorization",
        "auth",
    }
    sanitized = {}
    for key, value in params.items():
        if key.lower() in sensitive_keys:
            sanitized[key] = "***REDACTED***"
        else:
            sanitized[key] = value
    return sanitized
