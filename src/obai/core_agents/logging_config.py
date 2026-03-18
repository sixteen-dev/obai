"""File-based logging configuration for OBaI agents.

Writes daily rotating log files to ~/.obai/logs/ so system behavior
can be reviewed when running autonomously. All existing logger.info() /
logger.warning() / logger.exception() calls automatically flow to disk.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_configured = False

LOG_DIR = Path.home() / ".obai" / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
RETENTION_DAYS = 30


def configure_file_logging(
    level: int = logging.INFO,
    log_dir: Path | None = None,
) -> Path:
    """Add a daily rotating file handler to the root logger.

    Safe to call multiple times — only configures once.

    Args:
        level: Minimum log level for the file handler.
        log_dir: Override log directory (default: ~/.obai/logs/).

    Returns:
        Path to the active log file.
    """
    global _configured  # noqa: PLW0603
    if _configured:
        return _active_log_path(log_dir or LOG_DIR)

    target_dir = log_dir or LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    log_file = target_dir / "obai.log"

    handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=RETENTION_DAYS,
        encoding="utf-8",
        utc=False,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # Ensure root logger level doesn't suppress our file handler
    if root_logger.level > level:
        root_logger.setLevel(level)

    _configured = True

    logging.getLogger(__name__).info(
        "File logging enabled: %s (retention=%d days)", log_file, RETENTION_DAYS
    )

    return log_file


def _active_log_path(log_dir: Path) -> Path:
    """Return the path to the current log file."""
    return log_dir / "obai.log"
