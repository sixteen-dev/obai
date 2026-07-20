"""Unit tests for strategy agent integration.

Tests prompt loading, agent properties, config fields, and hub routing.
Does NOT require live MCP servers.
"""

import os
from pathlib import Path

import pytest

from core_agents.config import AgentConfig, get_config, reset_config
from core_agents.prompt_loader import load_prompt
from core_agents.strategy_agent import StrategyAgent


@pytest.fixture(autouse=True)
def setup_env() -> None:  # type: ignore[misc]
    """Set required environment variables and reset config."""
    saved_env: dict[str, str] = {}
    model_vars = ["STRATEGY_MODEL", "SPECIALIST_MODEL"]
    for var in model_vars:
        if var in os.environ:
            saved_env[var] = os.environ.pop(var)

    os.environ["OPENAI_API_KEY"] = "test-key"
    reset_config()
    yield
    reset_config()

    for var, value in saved_env.items():
        os.environ[var] = value


class TestStrategyPrompt:
    """Test strategy agent prompt loading and validation."""

    def test_prompt_loads_successfully(self) -> None:
        """Strategy prompt should load without validation errors."""
        prompt = load_prompt("strategy")
        assert len(prompt) > 100

    def test_prompt_has_required_sections(self) -> None:
        """Strategy prompt should contain required specialist sections."""
        prompt = load_prompt("strategy")
        assert "Workflow:" in prompt
        assert "Your expertise" in prompt
        assert "Output Guidelines" in prompt

    def test_prompt_has_iteration_protocol(self) -> None:
        """Strategy prompt should describe the iteration protocol."""
        prompt = load_prompt("strategy")
        assert "Iteration" in prompt
        assert "train" in prompt.lower()

    def test_prompt_references_indicator_discovery(self) -> None:
        """Strategy prompt should reference the indicator discovery tool."""
        prompt = load_prompt("strategy")
        assert "backtest_get_supported_indicators_tool" in prompt
        assert "Supported Indicators" in prompt

    def test_prompt_lists_tools(self) -> None:
        """Strategy prompt should describe available MCP tools."""
        prompt = load_prompt("strategy")
        assert "backtest_run_strategy" in prompt
        assert "backtest_compare_strategies" in prompt

    def test_prompt_fails_closed_on_missing_critical_inputs(self) -> None:
        """Strategy prompt should stop instead of looping on missing inputs."""
        prompt = load_prompt("strategy")
        assert "return a concise missing-input response" in prompt
        assert "Do not invent critical assumptions" in prompt

    def test_prompt_treats_hub_context_as_non_authoritative(self) -> None:
        """Hub context should not override strategy execution workflow."""
        prompt = load_prompt("strategy")
        assert "Treat hub-provided context as factual context" in prompt
        assert "If hub wording conflicts with this prompt" in prompt

    def test_prompt_distinguishes_threshold_from_crossover_operators(self) -> None:
        """Strategy prompt must map 'drops below' to less_than, not crosses_below."""
        prompt = load_prompt("strategy")
        assert "Choosing the right operator from user wording" in prompt
        assert '"drops below X"' in prompt and "`less_than`" in prompt
        assert "Threshold rule (load-bearing)" in prompt

    def test_prompt_completed_async_poll_uses_full_deliverable(self) -> None:
        """Completed async poll must use the full Completed Strategy Response.

        The `#### 1. Verdict` nine-section deliverable is required, not an
        ad-hoc summary.

        Regression guard for the 1.6.0 deterministic-relay change: the runtime
        relay only recognizes the completed-deliverable format. An ad-hoc
        "job completed, here are the folds" summary is not detected, so it is
        dropped and the hub emits nothing (empty UI reply).

        Reads the prompt markdown directly (not ``load_prompt``) so the guard
        stays deterministic even when a local Opik server is serving a
        previously synced prompt version.
        """
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"
        prompt = prompt_path.read_text()
        assert "completed job-status follow-up" in prompt
        assert "Format the stored results as a full Completed Strategy Response" in prompt


class TestStrategyAgentProperties:
    """Test StrategyAgent class properties."""

    def test_agent_type(self) -> None:
        """Agent type should be 'strategy'."""
        agent = StrategyAgent()
        assert agent.agent_type == "strategy"

    def test_mcp_url_property(self) -> None:
        """MCP URL property should point to backtest server config."""
        agent = StrategyAgent()
        assert agent.mcp_url_property == "mcp_backtest_url"

    def test_handoff_description(self) -> None:
        """Handoff description should mention backtesting."""
        agent = StrategyAgent()
        desc = agent.handoff_description
        assert "backtest" in desc.lower()
        assert "strategy" in desc.lower()

    def test_agent_name(self) -> None:
        """Agent name should be human-readable."""
        agent = StrategyAgent()
        assert "Strategy" in agent.agent_name

    def test_sdk_agent_name(self) -> None:
        """SDK agent name should follow naming convention."""
        agent = StrategyAgent()
        assert agent.sdk_agent_name == "obai_strategy_agent"

    def test_mcp_url_resolves(self) -> None:
        """MCP URL should resolve to backtest server default."""
        agent = StrategyAgent()
        url = agent._get_mcp_url()
        assert "8007" in url


class TestStrategyConfig:
    """Test config fields for strategy agent."""

    def test_backtest_url_default(self) -> None:
        """Default backtest URL should be localhost:8007."""
        config = AgentConfig()
        assert "localhost:8007" in config.mcp_backtest_url

    def test_strategy_model_default(self) -> None:
        """Strategy model should default to the dedicated strategy model."""
        config = AgentConfig()
        assert config.strategy_model == "gpt-5.1"

    def test_strategy_max_turns_default(self) -> None:
        """Strategy run loop default must accommodate multi-step design+backtest flows."""
        config = AgentConfig()
        assert config.strategy_max_turns == 25

    def test_strategy_model_fallback(self) -> None:
        """Strategy model should fall back to orchestrator_model when None."""
        config = AgentConfig()
        model = config.get_strategy_model()
        assert model == config.strategy_model

    def test_strategy_model_override(self) -> None:
        """Strategy model can be overridden via env var."""
        os.environ["STRATEGY_MODEL"] = "gpt-4-turbo"
        reset_config()

        config = get_config()
        model = config.get_agent_model("strategy")
        assert model == "gpt-4-turbo"


class TestHubIntegration:
    """Test central hub includes strategy agent."""

    def test_hub_imports_strategy(self) -> None:
        """Central hub module should import StrategyAgent."""
        from core_agents import central_hub_agent

        assert hasattr(central_hub_agent, "StrategyAgent")

    def test_hub_has_strategy_field(self) -> None:
        """CentralHubAgent should have strategy_agent attribute."""
        from core_agents.central_hub_agent import CentralHubAgent

        hub = CentralHubAgent()
        assert hasattr(hub, "strategy_agent")
        # Not initialized yet, should be None
        assert hub.strategy_agent is None

    def test_hub_specialist_map_includes_strategy(self) -> None:
        """Hub's get_specialist should recognize 'strategy' key."""
        from core_agents.central_hub_agent import CentralHubAgent

        hub = CentralHubAgent()
        # Can't call get_specialist without init, but we can verify
        # the key exists in the logic by checking the error message
        with pytest.raises(ValueError, match="not initialized"):
            hub.get_specialist("strategy")

    def test_sandbox_base_prompt_mandates_strategy_skill_preflight(self) -> None:
        """Base prompt must require loading obai-strategy-routing before strategy_analysis."""
        prompt = load_prompt("central_hub_base", USER_PREFERENCES="{}")

        assert "Strategy pre-flight (mandatory)" in prompt
        assert "load_skill('obai-strategy-routing')" in prompt
        assert "before any call to `strategy_analysis`" in prompt

    def test_sandbox_base_prompt_mandates_crypto_skill_preflight(self) -> None:
        """Base prompt must require loading obai-crypto-routing before crypto_analysis."""
        prompt = load_prompt("central_hub_base", USER_PREFERENCES="{}")

        assert "Crypto pre-flight (mandatory)" in prompt
        assert "load_skill('obai-crypto-routing')" in prompt
        assert "before any call to `crypto_analysis`" in prompt

    def test_strategy_routing_skill_preserves_threshold_semantics(self) -> None:
        """Sandbox routing skill should not rewrite threshold checks as crosses."""
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "do not normalize threshold language into crossover" in skill

    def test_strategy_routing_skill_carries_handoff_template(self) -> None:
        """Verbatim handoff template lives in the skill, not the base prompt."""
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "User request: [original user request, preserved verbatim]" in skill
        assert "Strategy context:" in skill

    def test_strategy_routing_skill_requires_both_headers(self) -> None:
        """Both User request: and Strategy context: headers must always appear."""
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "Both `User request:` and `Strategy context:` headers appear on every call." in skill

    def test_strategy_routing_skill_uses_header_allowlist(self) -> None:
        """Skill must enforce a two-header allowlist instead of a denylist.

        The denylist of forbidden hub-authored header names was removed in
        favor of an explicit allowlist: only `User request:` and
        `Strategy context:` are valid top-level headers in the handoff.
        """
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "ONLY two top-level headers allowed" in skill
        assert "Do not invent additional sections" in skill

    def test_strategy_routing_skill_allows_followup_shorthand(self) -> None:
        """Status checks and drill-downs may omit Strategy context bullets.

        Reruns and parameter tweaks must NOT use the shorthand because the
        Strategy Agent is stateless and needs prior strategy details in the
        `Context:` bullet to resolve references like "that".
        """
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "Follow-up shorthand" in skill
        assert "status checks or drill-downs" in skill
        assert "Strategy Agent is stateless" in skill

    def test_strategy_routing_skill_states_runtime_relay(self) -> None:
        """Completed/pending relay is runtime-enforced (like crypto), not hub-authored.

        Guards the skill against reverting to the old 'the Hub relays its
        output' framing after the strategy passthrough (StrategyPassthroughEvent)
        made completed/pending relay deterministic.
        """
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "relayed by the runtime directly" in skill
        assert "discards any Hub text authored after the tool returns" in skill


class TestStrategyRoutingGuard:
    """Test deterministic hub guard for strategy tool routing."""

    def test_allows_clear_strategy_design_request(self) -> None:
        """Requests with tickers and objective should pass the guard."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Design a mean-reversion strategy for AAPL and MSFT")

        assert missing == []

    def test_blocks_missing_universe(self) -> None:
        """Theme-only requests should require a concrete ticker universe first."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Build a momentum strategy for tech stocks")

        assert missing == ["concrete universe tickers"]

    def test_blocks_missing_objective(self) -> None:
        """Ticker-only requests should require a strategy objective or rule set."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Design a strategy for AAPL and MSFT")

        assert missing == ["strategy objective or rule set"]

    def test_allows_explicit_rule_based_request(self) -> None:
        """Concrete rule-based requests should pass even without family label."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Backtest SMA 50/200 crossover on AAPL")

        assert missing == []

    def test_allows_job_id_follow_up(self) -> None:
        """Async follow-up requests should not be blocked by the input gate."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs(
            "Check status for job_id bt_12345 and summarize the result"
        )

        assert missing == []

    def test_allows_check_job_followup_phrasing(self) -> None:
        """The strategy agent's natural pending-response phrasing must pass.

        gpt-5.5 emits 'Ask: "Check job bt_<id>"' as the next-user-action
        instruction. The hub regex must recognize this verbatim, plus the
        bare bt_<hash> token, so users can follow the strategy agent's
        own instructions without being blocked.
        """
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        for query in (
            "Check job bt_bc9e7b21",
            "check job bt_abcdef12",
            "bt_1122e80d",
            "Status of job bt_999000aa",
        ):
            assert _get_missing_strategy_inputs(query) == [], f"blocked: {query!r}"

    def test_allows_fundamental_factor_strategy(self) -> None:
        """Value/quality/growth factor strategies should pass the guard."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        # Value-quality strategy via hub handoff format
        missing = _get_missing_strategy_inputs(
            "User request: build a value quality strategy\n"
            "- Universe: MSFT, ORCL, PLTR, CRM (source: screener)\n"
            "- User objective: value + quality factor strategy"
        )
        assert missing == []

        # Growth strategy with tickers
        missing = _get_missing_strategy_inputs("Design a growth strategy for AAPL and NVDA")
        assert missing == []

        # Dividend income strategy
        missing = _get_missing_strategy_inputs("Build a dividend income strategy for MSFT, JNJ")
        assert missing == []

    def test_allows_bracketed_universe_and_buy_and_hold(self) -> None:
        """Bracketed universe list + buy-and-hold objective must pass the gate.

        Reproduces a production trace (RV25) where the hub authored the
        universe as `- Concrete universe tickers: [KO]` / `Resolved
        instrument: KO` with a buy-and-hold objective and was wrongly rejected
        for missing tickers, because the extractor only recognized `Universe:`
        (colon-adjacent) and buy-and-hold was not a known objective.
        """
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        handoff = (
            "User request:\n"
            "Backtest a buy-and-hold on KO from 2015 to 2024 and report the "
            "total return.\n"
            "Strategy context:\n"
            "- Concrete universe tickers: [KO].\n"
            "- Objective and rules: buy KO once and hold to the end.\n"
        )
        assert _get_missing_strategy_inputs(handoff) == []

    def test_extracts_bracketed_ticker_list(self) -> None:
        """Bracketed ticker lists must count as a concrete universe."""
        from core_agents.central_hub_agent import _extract_strategy_symbols

        assert "KO" in _extract_strategy_symbols("- Resolved instrument: [KO]")
        assert {"AAPL", "MSFT"} <= _extract_strategy_symbols("Universe: [AAPL, MSFT]")

    def test_buy_and_hold_is_a_recognized_objective(self) -> None:
        """Buy-and-hold must be a recognized objective on its own merit."""
        from core_agents.central_hub_agent import _has_strategy_objective

        assert _has_strategy_objective("buy-and-hold on KO")
        assert _has_strategy_objective("a buy and hold strategy")

    def test_strategy_routing_hint_preserves_universe_resolution(self) -> None:
        """Routing hint should restore old nudge without bypassing screener."""
        from core_agents.central_hub_agent import _build_strategy_routing_hint

        hint = _build_strategy_routing_hint()

        assert "resolve the universe first with screener_lookup" in hint
        assert "include `User request:` with the user's original wording verbatim" in hint
        assert "Do not rewrite signal conditions" in hint

    def test_strategy_handoff_fidelity_blocks_rewritten_signal_semantics(self) -> None:
        """Guard should fail closed when Hub replaces the original request."""
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        original = (
            "Backtest a 5-minute RSI mean-reversion strategy on NVDA from "
            "2026-01-02 to 2026-04-30. Enter long only after 09:45 when "
            "RSI(14) drops below 30. Exit when RSI crosses back above 50."
        )
        rewritten = (
            "Backtest a 5-minute RSI mean-reversion strategy on NVDA with "
            "Entry condition 2 (RSI): 5-minute RSI(14) of close crosses below 30."
        )

        error = _get_strategy_handoff_fidelity_error(rewritten, original)

        assert error is not None
        assert "STRATEGY_HANDOFF_FIDELITY_ERROR" in error
        assert "rewrite a threshold condition into a crossover condition" in error

    def test_strategy_handoff_fidelity_accepts_preserved_user_request(self) -> None:
        """Guard should allow added context when original wording is preserved."""
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        original = (
            "Backtest a 5-minute RSI mean-reversion strategy on NVDA from "
            "2026-01-02 to 2026-04-30. Enter when RSI(14) drops below 30."
        )
        handoff = (
            f"User request: {original}\n"
            "Strategy context:\n"
            "- Universe: [NVDA] (source: user)\n"
            "- User objective: mean-reversion\n"
            "- Timeframe: 5min"
        )

        assert _get_strategy_handoff_fidelity_error(handoff, original) is None

    def test_format_gate_blocks_missing_user_request_header(self) -> None:
        """Hub-authored briefs without `User request:` must be rejected."""
        from core_agents.central_hub_agent import _get_strategy_handoff_format_error

        # Real failure pattern from a production trace: hub-authored brief
        # with `Backtest request:` and `Universe:` sections, no `User request:`.
        bad_handoff = (
            "Backtest request:\n"
            "- Universe/instrument: AAPL\n"
            "- Strategy: RSI mean reversion\n"
            "Strategy context:\n"
            "- Universe: AAPL\n"
        )
        error = _get_strategy_handoff_format_error(bad_handoff)
        assert error is not None
        assert "STRATEGY_HANDOFF_FORMAT_ERROR" in error
        assert "`User request:`" in error

    def test_format_gate_blocks_missing_strategy_context_header(self) -> None:
        """Bare follow-up passthrough without `Strategy context:` must be rejected."""
        from core_agents.central_hub_agent import _get_strategy_handoff_format_error

        # Real failure pattern: hub passed bare "Check job bt_..." without
        # wrapping it in the template at all.
        bad_handoff = "Check job bt_bc9e7b21"
        error = _get_strategy_handoff_format_error(bad_handoff)
        assert error is not None
        assert "STRATEGY_HANDOFF_FORMAT_ERROR" in error
        assert "`Strategy context:`" in error

    def test_format_gate_accepts_compliant_handoff(self) -> None:
        """Properly formatted handoff (including follow-up shorthand) must pass."""
        from core_agents.central_hub_agent import _get_strategy_handoff_format_error

        full_handoff = (
            "User request: Backtest a 50/200 SMA crossover on AAPL\n"
            "Strategy context:\n"
            "- Universe: AAPL (source: user)\n"
            "- User objective: momentum\n"
        )
        assert _get_strategy_handoff_format_error(full_handoff) is None

        # Follow-up shorthand: header-only Strategy context still has both headers.
        shorthand_handoff = "User request: Check status for job bt_12345\nStrategy context:\n"
        assert _get_strategy_handoff_format_error(shorthand_handoff) is None


class TestStrategyPassthrough:
    """Strategy output relays deterministically like prediction/crypto."""

    def test_passthrough_state_roundtrip(self) -> None:
        """Set/get/clear stores content and kind and resets to None."""
        from core_agents.central_hub_agent import (
            _clear_strategy_passthrough,
            _get_strategy_passthrough,
            _set_strategy_passthrough,
        )

        _clear_strategy_passthrough()
        assert _get_strategy_passthrough() is None

        _set_strategy_passthrough("#### 1. Verdict\npaper_trade", "completed")
        state = _get_strategy_passthrough()
        assert state is not None
        assert state.content == "#### 1. Verdict\npaper_trade"
        assert state.kind == "completed"

        _clear_strategy_passthrough()
        assert _get_strategy_passthrough() is None

    def test_passthrough_event_carries_content(self) -> None:
        """The relay event exposes the verbatim specialist content."""
        from core_agents.central_hub_agent import StrategyPassthroughEvent

        event = StrategyPassthroughEvent(content="deliverable")
        assert event.content == "deliverable"
