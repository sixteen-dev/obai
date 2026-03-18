"""Configuration management for portfolio-server."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_version() -> str:
    """Read version from VERSION file."""
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Keys (loaded from environment variables)
    fmp_api_key: str = ""

    # Market Data Server (for price data)
    market_data_server_url: str = Field(default="http://localhost:8002")

    # Server Configuration
    server_name: str = "portfolio-server"
    server_version: str = Field(default_factory=_read_version)

    # Transport Configuration
    transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(default="streamable-http")
    host: str = Field(default="0.0.0.0")  # noqa: S104
    port: int = Field(default=8006)

    # Cache TTL Configuration (in hours)
    etf_holdings_cache_ttl_hours: int = Field(
        default=24,
        description="TTL for ETF holdings cache (holdings update quarterly)",
    )
    treasury_rates_cache_ttl_hours: int = Field(
        default=4,
        description="TTL for Treasury rates cache (rates update daily)",
    )
    economic_indicators_cache_ttl_hours: int = Field(
        default=4,
        description="TTL for economic indicators cache (inflation, etc.)",
    )

    # Logging
    log_level: str = Field(default="INFO")


# Global settings instance
_settings: Settings | None = None


def load_settings() -> Settings:
    """Load settings from environment variables and .env file.

    Returns:
        Configured Settings instance.

    """
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def get_settings() -> Settings:
    """Get cached settings instance (must call load_settings first).

    Returns:
        Cached Settings instance.

    Raises:
        RuntimeError: If settings have not been loaded yet.

    """
    if _settings is None:
        msg = "Settings not loaded - call load_settings() first"
        raise RuntimeError(msg)
    return _settings
