"""Tests for research-server configuration."""

from __future__ import annotations

from src.config import Settings


class TestSettings:
    def test_default_port(self):
        settings = Settings(exa_api_key="test")
        assert settings.port == 8008

    def test_default_server_name(self):
        settings = Settings(exa_api_key="test")
        assert settings.server_name == "research-server"

    def test_default_num_results(self):
        settings = Settings(exa_api_key="test")
        assert settings.default_num_results == 8

    def test_default_transport(self):
        settings = Settings(exa_api_key="test")
        assert settings.transport == "streamable-http"
