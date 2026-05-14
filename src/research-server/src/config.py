"""Configuration management for research-server."""

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

    # Exa API
    exa_api_key: str = ""

    # Server Configuration
    server_name: str = "research-server"
    server_version: str = Field(default_factory=_read_version)

    # Transport Configuration
    transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(
        default="streamable-http",
    )
    host: str = Field(default="0.0.0.0")  # noqa: S104
    port: int = Field(default=8008)

    # Exa Defaults
    default_num_results: int = Field(default=8)
    max_highlight_chars: int = Field(default=4000)
    max_response_chars: int = Field(default=40000)

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
