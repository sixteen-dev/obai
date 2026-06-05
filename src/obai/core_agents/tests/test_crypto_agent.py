"""Unit tests for Crypto Agent wiring and prompts."""

from __future__ import annotations

from contextvars import copy_context
from pathlib import Path

from core_agents.central_hub_agent import (
    _clear_crypto_passthrough,
    _get_crypto_passthrough,
    _get_crypto_preflight_error,
    _set_crypto_passthrough,
)
from core_agents.crypto_agent import CryptoAgent


class TestCryptoAgentInitialization:
    """Test Crypto Agent static configuration."""

    @staticmethod
    def _read_prompt_file(name: str) -> str:
        prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
        return (prompts_dir / f"{name}.md").read_text()

    @staticmethod
    def _read_hub_skill(name: str) -> str:
        skills_dir = Path(__file__).resolve().parents[1] / "hub_skills"
        return (skills_dir / name / "SKILL.md").read_text()

    def test_agent_creation(self) -> None:
        """Test agent can be created without connecting to MCP."""
        agent = CryptoAgent()
        assert agent.agent_type == "crypto"
        assert agent.mcp_url_property == "mcp_crypto_url"
        assert agent.sdk_agent_name == "obai_crypto_agent"

    def test_handoff_description_states_coinbase_spot_scope(self) -> None:
        """Handoff description should keep crypto v1 narrow."""
        desc = CryptoAgent().handoff_description
        assert "Coinbase spot" in desc
        assert "funding rates" in desc
        assert "live order placement" in desc

    def test_crypto_prompt_is_coinbase_only(self) -> None:
        """Crypto prompt should not imply multi-provider v1 support."""
        prompt = self._read_prompt_file("crypto")
        assert "Coinbase Advanced Trade public market data" in prompt
        assert "Never:" in prompt
        assert "Switch providers silently" in prompt
        assert "Authenticate or request Coinbase keys" in prompt
        assert "### Product lookup / metadata" in prompt
        assert "### Backtest / artifact response" in prompt
        assert "Use concise markdown tables for structured market data" in prompt

    def test_crypto_skill_has_terminal_relay_rules(self) -> None:
        """Hub skill should preserve crypto terminal output."""
        skill = self._read_hub_skill("obai-crypto-routing")
        assert "`crypto_analysis` is a terminal author" in skill
        assert "__TERMINAL_TOOL_OUTPUT__:crypto_analysis:" in skill
        assert "job_id" in skill
        assert "artifact `fingerprint`" in skill

    def test_crypto_preflight_blocks_unsupported_executable_venue(self) -> None:
        """Executable crypto handoffs must not silently switch providers."""
        error = _get_crypto_preflight_error("Backtest a BTC strategy on Binance futures")

        assert error is not None
        assert "Coinbase Advanced Trade spot only" in error

    def test_crypto_preflight_requires_job_for_artifact_export(self) -> None:
        """Artifact exports require a completed crypto job ID."""
        error = _get_crypto_preflight_error("Export the BTC-USD Coinbase paper artifact")

        assert error is not None
        assert "crypto_bt_" in error

    def test_crypto_preflight_allows_research_snapshot(self) -> None:
        """Research and snapshot requests should not be over-gated."""
        assert _get_crypto_preflight_error("Compare BTC and ETH spot liquidity") is None

    def test_crypto_passthrough_contextvar_resets(self) -> None:
        """Crypto passthrough is run-scoped state, not an instance attribute."""
        _set_crypto_passthrough("result")
        assert _get_crypto_passthrough() == "result"

        _clear_crypto_passthrough()

        assert _get_crypto_passthrough() is None

    def test_crypto_passthrough_survives_copied_context(self) -> None:
        """Tool-task writes should be visible to the parent run context."""
        _clear_crypto_passthrough()
        child_context = copy_context()

        child_context.run(_set_crypto_passthrough, "child result")

        assert _get_crypto_passthrough() == "child result"
