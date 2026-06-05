"""Configuration management for crypto-server."""

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
    """Application settings loaded from environment variables only."""

    model_config = SettingsConfigDict(extra="ignore")

    server_name: str = "crypto-server"
    server_version: str = Field(default_factory=_read_version)

    transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(
        default="streamable-http",
    )
    host: str = Field(default="0.0.0.0")  # noqa: S104
    port: int = Field(default=8010)

    coinbase_market_base_url: str = Field(
        default="https://api.coinbase.com/api/v3/brokerage/market",
    )
    request_timeout: float = Field(default=15.0, ge=1.0)

    coinbase_default_read_limit: int = Field(default=600, ge=1)
    coinbase_local_safety_limit: int = Field(default=300, ge=1)
    coinbase_rate_window_seconds: float = Field(default=10.0, ge=1.0)
    coinbase_max_concurrent_requests: int = Field(default=4, ge=1)
    coinbase_max_retries: int = Field(default=4, ge=0)
    coinbase_backoff_base_seconds: float = Field(default=0.25, ge=0.01)
    coinbase_backoff_max_seconds: float = Field(default=8.0, ge=0.1)

    crypto_duckdb_path: str = Field(default="./data/crypto.duckdb")
    crypto_duckdb_memory_limit: str = Field(default="2GB")
    historical_cache_ttl_hours: int = Field(default=24, ge=1)
    max_missing_pct_execution: float = Field(default=0.001, ge=0.0, le=1.0)

    log_level: str = Field(default="INFO")


_settings: Settings | None = None


def load_settings() -> Settings:
    """Load settings from environment variables."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def get_settings() -> Settings:
    """Get cached settings instance."""
    if _settings is None:
        msg = "Settings not loaded - call load_settings() first"
        raise RuntimeError(msg)
    return _settings
