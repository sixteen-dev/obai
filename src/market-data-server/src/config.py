"""Configuration for market-data MCP server using pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_version() -> str:
    """Read version from VERSION file.

    Returns:
        Version string (e.g., '0.1.0')
    """
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API Keys (loaded from environment variables)
    fmp_api_key: str = ""

    # FMP API Configuration
    fmp_base_url: str = "https://financialmodelingprep.com/stable"

    # Server Configuration
    server_name: str = "market-data-server"
    server_version: str = Field(default_factory=_read_version)

    # Transport Configuration (for deployment)
    transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(default="streamable-http")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)


# Global settings instance
_settings: Settings | None = None


def load_settings() -> Settings:
    """Load settings from environment variables.

    Returns:
        Configured Settings instance
    """
    global _settings

    if _settings is None:
        _settings = Settings()

    return _settings


def get_settings() -> Settings:
    """Get cached settings instance (must call load_settings first).

    Returns:
        Cached Settings instance

    Raises:
        RuntimeError: If load_settings hasn't been called yet
    """
    if _settings is None:
        raise RuntimeError("Settings not loaded - call load_settings() first")

    return _settings
