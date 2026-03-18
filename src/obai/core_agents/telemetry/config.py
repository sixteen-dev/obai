"""Telemetry configuration for DynamoDB trace storage.

Environment variables:
    TELEMETRY_ENABLED: Enable/disable telemetry (default: true)
    TELEMETRY_TABLE_NAME: DynamoDB table name (default: obai_traces_dev)
    TELEMETRY_REGION: AWS region (default: us-east-2)
    TELEMETRY_ENDPOINT_URL: Custom endpoint for LocalStack (default: None)
    TELEMETRY_TTL_DAYS: Trace retention in days (default: 90)
    TELEMETRY_ENVIRONMENT: Environment name for tagging (default: dev)
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class TelemetryConfig(BaseSettings):
    """Telemetry configuration with environment variable support."""

    model_config = SettingsConfigDict(
        env_prefix="TELEMETRY_",
        case_sensitive=False,
    )

    # Enable/disable
    enabled: bool = False

    # DynamoDB settings
    table_name: str = "obai_traces_dev"
    region: str = "us-east-2"
    endpoint_url: str | None = None  # For LocalStack or local DynamoDB

    # Retention
    ttl_days: int = 90

    # Environment tagging
    environment: str = "dev"

    # Retry settings for async writes
    max_retries: int = 3
    retry_delay_seconds: float = 0.5


@lru_cache
def get_telemetry_config() -> TelemetryConfig:
    """Get telemetry configuration singleton.

    Returns:
        TelemetryConfig instance loaded from environment.
    """
    return TelemetryConfig()


def reset_telemetry_config() -> None:
    """Reset config cache. Useful for testing."""
    get_telemetry_config.cache_clear()
