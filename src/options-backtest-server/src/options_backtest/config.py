"""Server settings, read from the environment and ``.env`` in ``main()`` only (ADR 0003 §1.2).

The variables carry no prefix, like every server in the fleet: ``TRANSPORT``, ``HOST``, ``PORT``
and ``LOG_LEVEL``. The deployment mode is a constant, not a setting: under the Individual data
license local single-user mode is the only permitted deployment (design §16.1, ADR 0001 §9).
"""

from __future__ import annotations

import logging
from typing import Final, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEPLOYMENT_MODE: Final = "local_single_user"


class Settings(BaseSettings):
    """Transport, bind address and log level of the MCP server.

    Attributes:
        transport: Streamable HTTP (``http`` is fastmcp's alias for it). stdio would share the
            protocol channel with the JSON logs on stdout, and SSE cannot run stateless.
        host: Bind address; loopback, so a bare ``python -m options_backtest.server`` stays
            local. The container sets ``0.0.0.0`` and compose binds the host side to loopback.
        port: TCP port; 8012 (design §1).
        log_level: A standard logging level name, stored upper-case.

    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    transport: Literal["http", "streamable-http"] = "streamable-http"
    host: str = "127.0.0.1"
    port: int = Field(default=8012, ge=1, le=65535)
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        level = value.upper()
        if level not in logging.getLevelNamesMapping():
            raise ValueError(f"log_level must be a logging level name, got {value!r}")
        return level
