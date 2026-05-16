"""Structured logging configuration for knowledge-base-server."""

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured logging with structlog.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

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
    """Get a configured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured structlog BoundLogger
    """
    return structlog.get_logger(name)


def log_error(logger: Any, error: Exception, context: dict[str, Any] | None = None) -> None:
    """Log an exception with optional context.

    If `context` contains an ``event`` key, it becomes the structlog event
    name; otherwise the default name is ``"error"``.

    Args:
        logger: structlog BoundLogger
        error: Exception to log
        context: Optional extra fields (``event`` is special)
    """
    fields = dict(context or {})
    event = fields.pop("event", "error")
    fields["error_type"] = type(error).__name__
    fields["error_message"] = str(error)
    logger.exception(event, **fields)
