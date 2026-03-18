"""Structured logging configuration for fundamentals-server."""

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured logging with structlog.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Configure structlog processors
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
        Configured structlog logger
    """
    return structlog.get_logger(name)


def log_api_call(
    logger: Any,
    service: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Log an external API call.

    Args:
        logger: Logger instance
        service: Service name (e.g., 'fmp')
        endpoint: API endpoint
        params: Request parameters (will be sanitized)
    """
    sanitized_params = _sanitize_params(params or {})
    logger.info(
        "api_call",
        service=service,
        endpoint=endpoint,
        params=sanitized_params,
    )


def log_auth_event(
    logger: Any,
    event_type: str,
    success: bool,
    client_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Log authentication/authorization events for security auditing.

    Args:
        logger: Logger instance
        event_type: Type of auth event (login, token_validation, access_denied)
        success: Whether the event succeeded
        client_id: Client identifier
        reason: Reason for failure (if applicable)
    """
    logger.info(
        "auth_event",
        event_type=event_type,
        success=success,
        client_id=client_id,
        reason=reason,
    )


def log_tool_invocation(
    logger: Any,
    tool_name: str,
    params: dict[str, Any],
    client_id: str | None = None,
) -> None:
    """Log MCP tool invocation for auditing.

    Args:
        logger: Logger instance
        tool_name: Name of the tool being invoked
        params: Tool parameters
        client_id: Client identifier
    """
    sanitized_params = _sanitize_params(params)
    logger.info(
        "tool_invocation",
        tool_name=tool_name,
        params=sanitized_params,
        client_id=client_id,
    )


def log_error(
    logger: Any,
    error: Exception,
    context: dict[str, Any] | None = None,
) -> None:
    """Log an error with full context.

    Args:
        logger: Logger instance
        error: Exception that occurred
        context: Additional context (avoid using 'event' key as it's reserved)
    """
    error_context = {
        "error_type": type(error).__name__,
        "error_message": str(error),
    }

    # Merge context, renaming 'event' to 'error_event' if present to avoid conflicts
    if context:
        for key, value in context.items():
            if key == "event":
                error_context["error_event"] = value
            else:
                error_context[key] = value

    logger.error(
        "error_occurred",
        **error_context,
        exc_info=True,
    )


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Sanitize parameters to remove sensitive data.

    Args:
        params: Parameters to sanitize

    Returns:
        Sanitized parameters
    """
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
