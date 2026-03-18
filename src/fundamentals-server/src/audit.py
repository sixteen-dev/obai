"""Security auditing and monitoring for MCP server."""

from datetime import UTC, datetime
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)


def log_rate_limit_violation(
    tool_name: str, client_id: str | None, limit: int, window_seconds: int
) -> None:
    """Log potential rate limit violations for security monitoring.

    Args:
        tool_name: Name of the tool being rate limited
        client_id: Client identifier
        limit: Request limit
        window_seconds: Time window in seconds
    """
    logger.warning(
        "rate_limit_violation",
        tool_name=tool_name,
        client_id=client_id,
        limit=limit,
        window_seconds=window_seconds,
        timestamp=datetime.now(UTC).isoformat(),
    )


def log_suspicious_activity(
    event_type: str, details: dict[str, Any], severity: str = "medium"
) -> None:
    """Log suspicious activity for security analysis.

    Args:
        event_type: Type of suspicious event
        details: Event details
        severity: Severity level (low, medium, high, critical)
    """
    logger.warning(
        "suspicious_activity",
        event_type=event_type,
        severity=severity,
        timestamp=datetime.now(UTC).isoformat(),
        **details,
    )


def log_data_access(
    resource_type: str, resource_id: str, client_id: str | None, action: str
) -> None:
    """Log data access for compliance and auditing.

    Args:
        resource_type: Type of resource accessed (e.g., 'financial_statement')
        resource_id: Resource identifier (e.g., 'AAPL_income_2024')
        client_id: Client identifier
        action: Action performed (read, write, delete)
    """
    logger.info(
        "data_access_audit",
        resource_type=resource_type,
        resource_id=resource_id,
        client_id=client_id,
        action=action,
        timestamp=datetime.now(UTC).isoformat(),
    )
