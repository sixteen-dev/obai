"""Configuration for knowledge-base MCP server."""

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


def _default_corpus_db_path() -> Path:
    """Default corpus.db path: alongside the server package (sibling to src/)."""
    return Path(__file__).parent.parent / "corpus.db"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Corpus DB path. Default resolves alongside the server package; an env var
    # can override for tests or non-standard deployments.
    corpus_db_path: Path = Field(default_factory=_default_corpus_db_path)

    # Server identity
    server_name: str = "knowledge-base-server"
    server_version: str = Field(default_factory=_read_version)

    # Transport / network
    transport: Literal["stdio", "http", "sse", "streamable-http"] = Field(default="streamable-http")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8011)

    # Logging
    log_level: str = Field(default="INFO")


_settings: Settings | None = None


def load_settings() -> Settings:
    """Load settings from environment variables (called once at boot)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_settings() -> Settings:
    """Return the loaded settings. Raises if load_settings() was never called."""
    if _settings is None:
        raise RuntimeError("Settings not loaded; call load_settings() during bootstrap")
    return _settings
