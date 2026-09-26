"""Structured JSON logging to stdout (ADR 0003 §1.3; the backtest-server configuration).

The server makes no outbound HTTP call, so no client library's request logging is silenced.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(log_level: str) -> None:
    """Send stdlib and structlog records to stdout as JSON lines at ``log_level``.

    ``basicConfig`` adds its stdout handler only to a root logger that has none, so a second
    call adds nothing; the level is set on the root logger either way.

    Args:
        log_level: A standard logging level name, any case.

    Raises:
        ValueError: If ``log_level`` is not a logging level name.

    """
    level = logging.getLevelNamesMapping().get(log_level.upper())
    if level is None:
        raise ValueError(f"log_level must be a logging level name, got {log_level!r}")
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    logging.getLogger().setLevel(level)
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


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return the structlog logger named ``name``.

    Args:
        name: Logger name, normally the calling module's ``__name__``.

    Returns:
        The logger; it follows whatever configuration is current at its first use.

    Raises:
        ValueError: If ``name`` is empty.

    """
    if not name:
        raise ValueError("get_logger needs a logger name")
    return structlog.stdlib.get_logger(name)
