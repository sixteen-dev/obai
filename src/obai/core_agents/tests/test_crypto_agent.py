"""Unit tests for Crypto Agent wiring and prompts."""

from __future__ import annotations

import asyncio
import json
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace

from agents import FunctionTool
from agents.tool_context import ToolContext

from core_agents.central_hub_agent import (
    CentralHubAgent,
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

    def test_crypto_skill_owns_scope_rejection_wording(self) -> None:
        """The Hub composes the refusal; the pre-flight token is never the answer."""
        skill = self._read_hub_skill("obai-crypto-routing")
        assert "MISSING_CRYPTO_INPUTS" in skill
        assert "Never relay it" in skill
        assert "every capability" in skill

    def test_crypto_preflight_blocks_unsupported_executable_venue(self) -> None:
        """Executable crypto handoffs must not silently switch providers."""
        error = _get_crypto_preflight_error("Backtest a BTC strategy on Binance futures")

        assert error is not None
        assert "Coinbase Advanced Trade spot only" in error

    def test_crypto_preflight_routes_artifact_export_to_specialist(self) -> None:
        """Export eligibility is the specialist's contract; the hub must not pre-classify it."""
        assert _get_crypto_preflight_error("Export the BTC-USD Coinbase paper artifact") is None

    def test_crypto_preflight_allows_research_snapshot(self) -> None:
        """Research and snapshot requests should not be over-gated."""
        assert _get_crypto_preflight_error("Compare BTC and ETH spot liquidity") is None

    def test_crypto_preflight_ignores_paper_context_word(self) -> None:
        """`paper` as a context word must not trigger the artifact/job-id gate."""
        order_book = (
            "Check Coinbase spot SOL-USD and AVAX-USD order books before a paper trade. "
            "Compare top-of-book spread and visible depth, then tell which market looks "
            "less fragile right now."
        )
        paper_ledger_context = (
            "Before I risk $10,000 notional in the internal paper ledger, inspect the "
            "Coinbase BTC-USD order book at depth 50."
        )
        live_order = (
            "If the current Coinbase SOL-USD quote and order book look good, place a live "
            "buy order for $5,000 notional with a paper-simulation checklist."
        )

        assert _get_crypto_preflight_error(order_book) is None
        assert _get_crypto_preflight_error(paper_ledger_context) is None
        assert _get_crypto_preflight_error(live_order) is None

    def test_crypto_preflight_allows_backtest_asking_artifact_eligibility(self) -> None:
        """A new backtest that asks whether an artifact would be eligible must run."""
        query = (
            "Run a Coinbase execution-grade backtest for BTC-USD using a daily spot "
            "trend-following rule: fast_window 20, slow_window 60. I need the verdict, "
            "key metrics, source_quality, and whether an internal paper-ledger artifact "
            "would be eligible."
        )

        assert _get_crypto_preflight_error(query) is None

    def test_crypto_preflight_allows_stored_job_status_follow_up(self) -> None:
        """A stored job id is a concrete target; the hub must not demand a symbol.

        The gate exists so an executable backtest cannot start without a
        product. A status poll carries no product because the specialist
        resolves it from the stored job, and blocking it strands every
        async crypto backtest at its first follow-up turn.
        """
        poll = (
            "Check crypto backtest job crypto_bt_7197be75807dc23e and return its stored "
            "status, closed round-trip trade count, hit rate, fees, and realized P&L "
            "without recomputing a backtest."
        )

        assert _get_crypto_preflight_error(poll) is None

    def test_crypto_preflight_ignores_bare_job_id_mention(self) -> None:
        """`job_id` alone is follow-up intent, which the specialist owns."""
        assert _get_crypto_preflight_error("What is the job_id for my last run?") is None

    def test_crypto_preflight_accepts_stored_artifact_id_as_target(self) -> None:
        """A stored artifact id is as concrete a target as a job id.

        Replay of the CORE-CRYPTO-VALIDATE handoff: the hub named the exact
        artifact_id and its expected fingerprint, and the gate still demanded
        a product symbol, so the artifact was never validated. The product is
        encoded in the artifact id and the specialist resolves it from there.
        """
        handoff = (
            "Validate the existing stored internal Coinbase paper-ledger artifact with "
            "exact artifact_id `btc_usd_coinbase_1d_spot_trend_follow_v1` and expected "
            "fingerprint `7d2e3725f65f7607d483a0bc14958fe50491b0dcaa0d26d130c293bea0443bad`. "
            "Load the artifact from storage; do not reconstruct it from conversational "
            "memory and do not create or export a new artifact. Recompute/verify the stored "
            "artifact fingerprint against every load-bearing stored field covering venue, "
            "product, strategy, risk, and execution configuration. Return the exact "
            "artifact_id, stored fingerprint, validation status, and reasons, including any "
            "mismatched or missing load-bearing fields."
        )

        assert _get_crypto_preflight_error(handoff) is None

    def test_crypto_preflight_reads_future_as_a_date_word_not_an_instrument(self) -> None:
        """The word "future" describes a date far more often than a contract.

        A candle request whose whole point is clamping a future-dated end
        was refused as a derivatives request, and the refusal shipped as
        the answer. Same context-word trap the "paper" exclusion documents.
        """
        clamp_request = (
            "Coinbase Advanced Trade public market-data request only; no backtest and no "
            "artifact export. Product: BTC-USD. Granularity: ONE_DAY / daily UTC candles. "
            "Distinguish endpoint clamping caused by the future requested end from any "
            "genuine missing interval within otherwise covered history."
        )

        assert _get_crypto_preflight_error(clamp_request) is None

    def test_crypto_preflight_names_every_unsupported_capability(self) -> None:
        """A multi-part request needs a multi-part refusal.

        The gate stopped at the first match, so a request spanning venue,
        derivatives and on-chain scope produced a one-item rejection that
        never mentioned the other five capabilities it declined.
        """
        handoff = (
            "Backtest request. Analyze Binance BTC perpetual funding, open interest and "
            "liquidation clusters at 10x leverage, plus ETH DeFi TVL and whale-wallet "
            "on-chain flows, then export a Coinbase paper-ledger artifact."
        )

        error = _get_crypto_preflight_error(handoff)

        assert error is not None
        assert "Binance" in error
        for capability in ("perpetual", "funding", "open interest", "liquidation", "DeFi"):
            assert capability.lower() in error.lower(), f"{capability} not named in refusal"

    def test_crypto_preflight_still_blocks_derivatives(self) -> None:
        """The instrument gate must keep refusing genuine non-spot scope."""
        for handoff in (
            "Backtest BTC-USD futures on Coinbase with a 20/60 SMA rule",
            "Backtest BTC-USD perpetual funding and open interest",
        ):
            assert _get_crypto_preflight_error(handoff) is not None

    def test_crypto_preflight_still_blocks_backtest_without_any_target(self) -> None:
        """Removing the follow-up gate must not open the missing-symbol hole."""
        error = _get_crypto_preflight_error("Run a Coinbase spot backtest with a 20/60 SMA rule")

        assert error is not None
        assert "concrete product or asset symbol" in error


class TestCryptoToolWrapper:
    """Test the hub-side crypto tool wrapper."""

    @staticmethod
    def _hub() -> CentralHubAgent:
        hub = object.__new__(CentralHubAgent)
        hub.crypto_agent = SimpleNamespace(agent=SimpleNamespace(name="stub_crypto"))  # type: ignore[assignment]
        return hub

    @staticmethod
    async def _invoke(tool: FunctionTool, arguments: str) -> str:
        context: ToolContext[None] = ToolContext(
            context=None,
            tool_name=tool.name,
            tool_call_id="call_test",
            tool_arguments=arguments,
        )
        return str(await tool.on_invoke_tool(context, arguments))

    def test_preflight_error_is_not_relayed_to_the_user(self) -> None:
        """`MISSING_CRYPTO_INPUTS` is a control signal, not an answer.

        Terminal-wrapping it shipped the raw token as the entire response.
        It must return to the hub the way strategy handoff errors do, so
        the routing skill composes the refusal.
        """
        _clear_crypto_passthrough()
        tool = self._hub()._build_crypto_tool()
        assert isinstance(tool, FunctionTool)

        arguments = json.dumps({"input": "Backtest a BTC strategy on Binance futures"})
        output = asyncio.run(self._invoke(tool, arguments))

        assert output.startswith("MISSING_CRYPTO_INPUTS")
        assert "__TERMINAL_TOOL_OUTPUT__" not in output
        assert _get_crypto_passthrough() is None


class TestCryptoPassthrough:
    """Test crypto passthrough context state."""

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
