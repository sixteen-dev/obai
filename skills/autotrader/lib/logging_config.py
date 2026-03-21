"""Structured logging configuration for autotrader skill.

Logs to JSONL files at {skill_root}/logs/autotrader_{date}.jsonl.
Path resolves relative to this file so the skill folder can live anywhere.

Stdout is reserved for script JSON output — all logging goes to files only.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import structlog

SKILL_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = SKILL_ROOT / "logs"

_configured = False


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog with daily JSONL file output.

    Args:
        log_level: Minimum log level (default INFO).

    """
    global _configured  # noqa: PLW0603
    if _configured:
        return

    LOGS_DIR.mkdir(exist_ok=True)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"autotrader_{today}.jsonl"

    # File handler only — stdout is reserved for script JSON output
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(getattr(logging, log_level.upper()))
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.setLevel(getattr(logging, log_level.upper()))

    # Suppress noisy HTTP client loggers
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
        cache_logger_on_first_use=False,
    )

    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a configured structlog logger.

    Args:
        name: Logger name (e.g., 'alpaca_client', 'risk').

    Returns:
        Bound structlog logger.

    """
    configure_logging()
    return structlog.get_logger(name)  # type: ignore[return-value]


def reset_logging() -> None:
    """Reset logging state. Used in tests only."""
    global _configured  # noqa: PLW0603
    _configured = False
    structlog.reset_defaults()
    logging.root.handlers.clear()
