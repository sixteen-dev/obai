"""Configuration management for backtest-server."""

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

    # API Keys
    fmp_api_key: str = ""

    # Server Configuration
    server_name: str = "backtest-server"
    server_version: str = Field(default_factory=_read_version)

    # Transport Configuration
    transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(
        default="streamable-http",
    )
    host: str = Field(default="0.0.0.0")  # noqa: S104
    port: int = Field(default=8007)

    # Data Storage (legacy Parquet path, kept for migration)
    backtest_data_dir: str = Field(default="./data/ohlcv")
    backtest_data_freshness_hours: int = Field(default=24)

    # DuckDB Configuration
    duckdb_path: str = Field(default="./data/backtest.duckdb")
    duckdb_memory_limit: str = Field(default="4GB")

    # Retention Limits (max years of history per timeframe)
    max_daily_years: int = Field(default=30)
    max_1hour_years: int = Field(default=5)
    max_15min_years: int = Field(default=2)
    max_5min_years: int = Field(default=2)

    # Disk Budget
    max_db_size_gb: float = Field(default=5.0, gt=0.0)

    # Operator-only confirmation token required to use the destructive
    # ``backtest_manage_storage_tool(action="prune", ...)``. Leave unset to
    # disable prune entirely; set to a long random string to enable.
    storage_admin_token: str = Field(default="")

    # Cache Configuration
    backtest_cache_dir: str = Field(default="./data/cache")
    backtest_cache_ttl_hours: int = Field(default=24)

    # Async / Job Configuration
    auto_async_threshold_seconds: float = Field(default=10.0, ge=0.0)
    job_result_ttl_seconds: int = Field(default=3600, ge=60)

    # Runtime Estimation Weights
    estimate_symbol_year_weight: float = Field(default=0.5, ge=0.0)
    estimate_indicator_weight: float = Field(default=0.1, ge=0.0)
    estimate_download_penalty: float = Field(default=2.0, ge=0.0)

    # Limits
    max_universe_size: int = Field(default=500)
    max_backtest_years: int = Field(default=30)

    # Logging
    log_level: str = Field(default="INFO")


# Global settings instance
_settings: Settings | None = None


def load_settings() -> Settings:
    """Load settings from environment variables and .env file."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def get_settings() -> Settings:
    """Get cached settings instance (must call load_settings first)."""
    if _settings is None:
        msg = "Settings not loaded - call load_settings() first"
        raise RuntimeError(msg)
    return _settings
