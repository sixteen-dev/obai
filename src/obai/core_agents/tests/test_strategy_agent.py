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

# The user request from the CORE-WALKFORWARD gate case, verbatim.
_WALKFORWARD_REQUEST = (
    "Run a frozen walk-forward test of a long-only SPY SMA(200) trend rule from "
    "2015-01-02 through 2024-12-31: five anchored training/test folds, at least 250 prior "
    "trading days of indicator warm-up for every test fold, next-open execution, 5 bps "
    "slippage and 1 bp commission per side. Report each fold's train and out-of-sample "
    "dates, warm-up coverage, trades, return, Sharpe and drawdown; then assess robustness "
    "without mixing train and test metrics."
)


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

    def test_walk_forward_reporting_names_every_stored_provenance_field(self) -> None:
        """The prompt must claim the fields the job payload now carries.

        The payload gained `strategy`, `fill_timing`, and per-fold
        `warmup_bars` precisely so a polled job stops answering "not available
        from stored result". That only helps if the reporting rule tells the
        agent to read them, and null must stay distinguishable from zero: zero
        pre-roll bars is a real finding about unprimed indicators.

        Reads the markdown directly for the same reason as the guard above.
        """
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"
        prompt = prompt_path.read_text()

        for field_name in ("`execution_config`", "`strategy`", "`fill_timing`", "`warmup_bars`"):
            assert field_name in prompt, field_name
        assert "rather than as zero" in prompt


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
        assert config.strategy_model == "gpt-5.6-terra"

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

    def test_strategy_routing_skill_documents_handoff_arguments(self) -> None:
        """The argument contract lives in the skill, not the base prompt."""
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "`user_request` — the user's wording, preserved verbatim." in skill
        assert "`universe` — the resolved tradable tickers, as a list." in skill
        assert "`context` — Hub-resolved facts, as bullet lines." in skill

    def test_strategy_routing_skill_states_the_runtime_assembles_the_handoff(self) -> None:
        """The Hub must not be asked to reproduce a text template.

        Reproducing an exact two-block layout in prose was the contract that
        never held: across 157 recorded hand-offs the Hub produced 34
        different universe labels and the mandated literal form zero times.
        """
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "The runtime assembles the hand-off" in skill
        assert "no text template to reproduce" in skill

    def test_strategy_routing_skill_keeps_user_rules_out_of_context(self) -> None:
        """Entry/exit/risk rules belong in user_request, never in context."""
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "It does not restate the user's entry/exit/risk rules" in skill
        assert "those belong inside `user_request`" in skill

    def test_strategy_routing_skill_allows_job_reference_follow_up(self) -> None:
        """Status checks pass an empty universe and context.

        Reruns and parameter tweaks must NOT use the shorthand because the
        Strategy Agent is stateless and needs prior strategy details in
        `context` to resolve references like "that".
        """
        skill_path = Path(__file__).parents[1] / "hub_skills" / "obai-strategy-routing" / "SKILL.md"
        skill = skill_path.read_text()

        assert "leave `universe` and `context` empty" in skill
        assert "needs no universe" in skill
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

        missing = _get_missing_strategy_inputs(
            "Design a mean-reversion strategy", ["AAPL", "MSFT"], ""
        )

        assert missing == []

    def test_blocks_missing_universe(self) -> None:
        """Theme-only requests should require a concrete ticker universe first."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Build a momentum strategy for tech stocks", [], "")

        assert missing == ["concrete universe tickers"]

    def test_blocks_whitespace_only_universe_entries(self) -> None:
        """A list of blank strings is not a resolved universe."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Build a momentum strategy", ["", "  "], "")

        assert missing == ["concrete universe tickers"]

    def test_blocks_missing_objective(self) -> None:
        """Ticker-only requests should require a strategy objective or rule set."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Look at these names", ["AAPL"], "")

        assert missing == ["strategy objective or rule set"]

    def test_objective_may_come_from_hub_context(self) -> None:
        """The objective counts whether the user or the Hub context states it."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs(
            "Look at these names", ["AAPL"], "- User objective: momentum"
        )

        assert missing == []

    def test_allows_explicit_rule_based_request(self) -> None:
        """Concrete rule-based requests should pass even without family label."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Backtest SMA 50/200 crossover", ["AAPL"], "")

        assert missing == []

    def test_allows_job_id_follow_up(self) -> None:
        """A stored job id is a concrete target, so no universe is required."""
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs(
            "Check status for job bt_bc9e7b21 and summarize the result", [], ""
        )

        assert missing == []

    def test_allows_bare_job_token_follow_up(self) -> None:
        """The strategy agent tells users to ask for the bare token; accept it.

        gpt-5.5 emits 'Ask: "Check job bt_<id>"' as its next-user-action
        instruction, so users follow the specialist's own wording.
        """
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        for query in ("Check job bt_bc9e7b21", "bt_1122e80d", "Status of job bt_999000aa"):
            assert _get_missing_strategy_inputs(query, [], "") == [], f"blocked: {query!r}"

    def test_prose_follow_up_without_a_job_token_still_needs_inputs(self) -> None:
        """Fuzzy follow-up intent is the specialist's call, not a hub gate.

        The hub may only test hard syntactic facts. "Is it done yet?" names
        nothing concrete, so the gate must fall through to the normal
        requirements rather than guessing that a prior job was meant.
        """
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs("Is it done yet?", [], "")

        assert missing == ["concrete universe tickers", "strategy objective or rule set"]

    def test_walkforward_prose_universe_no_longer_blocks_the_backtest(self) -> None:
        """Replay of CORE-WALKFORWARD: a prose universe bullet must not block.

        Every recorded gate run since 2026-07-17 rejected this hand-off for
        "concrete universe tickers" because the Hub wrote the universe as
        prose ("- SPY is the resolved US equity ETF universe.") instead of the
        one shape the old regex extractor recognised. The universe is now a
        typed argument, so how the Hub words its context cannot hide it.
        """
        from core_agents.central_hub_agent import _get_missing_strategy_inputs

        missing = _get_missing_strategy_inputs(
            _WALKFORWARD_REQUEST,
            ["SPY"],
            "- SPY is the resolved US equity ETF universe.",
        )

        assert missing == []

    def test_strategy_routing_hint_preserves_universe_resolution(self) -> None:
        """Routing hint should restore old nudge without bypassing screener."""
        from core_agents.central_hub_agent import _build_strategy_routing_hint

        hint = _build_strategy_routing_hint()

        assert "resolve the universe first with screener_lookup" in hint
        assert "pass `user_request` as the user's original wording" in hint
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
        """A verbatim user_request argument satisfies the fidelity gate."""
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        original = (
            "Backtest a 5-minute RSI mean-reversion strategy on NVDA from "
            "2026-01-02 to 2026-04-30. Enter when RSI(14) drops below 30."
        )

        assert _get_strategy_handoff_fidelity_error(original, original) is None

    def test_strategy_handoff_fidelity_ignores_a_trailing_annotation(self) -> None:
        """Metadata appended to the query is not part of the user's request.

        The regression gate appends a bracketed correlation marker that also
        tells the model not to repeat it. Demanding the Hub echo it back cost
        one rejected hand-off on every strategy case in the suite while
        proving nothing about signal fidelity.
        """
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        request = "Backtest a buy-and-hold on KO from 2015 to 2024."
        submitted = (
            f"{request}\n\n"
            "[OBaI regression correlation: regress:CORE-WALKFORWARD:00317b94. "
            "Do not repeat this marker.]"
        )

        assert _get_strategy_handoff_fidelity_error(request, submitted) is None

    def test_buy_and_hold_is_a_recognized_objective(self) -> None:
        """Buy-and-hold must be a recognized objective on its own merit."""
        from core_agents.central_hub_agent import _has_strategy_objective

        assert _has_strategy_objective("buy-and-hold on KO")
        assert _has_strategy_objective("a buy and hold strategy")


class TestStrategyHandoffRendering:
    """The runtime renders the hand-off the Strategy Agent reads."""

    def test_renders_canonical_two_block_structure(self) -> None:
        """Both headers and a bracketed universe are produced deterministically."""
        from core_agents.central_hub_agent import _render_strategy_handoff

        handoff = _render_strategy_handoff(
            "Backtest a buy-and-hold on KO.", ["KO"], "- Universe source: user."
        )

        assert handoff.startswith("User request:\nBacktest a buy-and-hold on KO.")
        assert "Strategy context:" in handoff
        assert "- Universe: [KO]" in handoff
        assert "- Universe source: user." in handoff

    def test_renders_multiple_tickers_as_one_bracketed_list(self) -> None:
        """A resolved universe is rendered in the shape the specialist expects."""
        from core_agents.central_hub_agent import _render_strategy_handoff

        handoff = _render_strategy_handoff("Rank these.", ["AAPL", "MSFT", "NVDA"], "")

        assert "- Universe: [AAPL, MSFT, NVDA]" in handoff

    def test_renders_follow_up_without_a_universe_line(self) -> None:
        """A job-status follow-up carries no universe, and must not invent one."""
        from core_agents.central_hub_agent import _render_strategy_handoff

        handoff = _render_strategy_handoff("Check job bt_bc9e7b21", [], "")

        assert "Universe" not in handoff
        assert handoff == "User request:\nCheck job bt_bc9e7b21\nStrategy context:"


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

    def test_every_non_empty_output_is_relayable(self) -> None:
        """Relay is decided by output being non-empty, never by its shape.

        The hub must not depend on specialist section headings: any non-empty
        response earns a marker label and is therefore relayed verbatim. This
        pins the generic property, so it must not assert a specific format.
        """
        from core_agents.central_hub_agent import _strategy_relay_kind

        shapes = [
            "#### 1. Verdict\npaper_trade — folds are mostly positive.",
            "Status\n\nJob ID  \nbt_a707b0de\n\nEstimated Time  \n≈50 seconds",
            # Completed async job-status follow-up: the shape that was dropped.
            "Status: completed  \nJob ID: bt_a707b0de  \n\n### Fold results (train)\n| Fold |",
            # Mode 3 diagnostic answer: carries neither literal, by design.
            "Supported indicators: SMA, EMA, RSI, MACD, ATR, ADX.",
            "Missing Inputs\nWhich universe should the strategy trade?",
            "The backtest engine rejected the date range: 2015-13-01 is not a valid date.",
            "I cannot model intraday tick data; the engine supports daily bars only.",
        ]
        for output in shapes:
            assert _strategy_relay_kind(output), f"no relay label for: {output[:60]!r}"

    def test_relay_kind_labels_are_descriptive_only(self) -> None:
        """The label distinguishes known shapes but never blocks relay."""
        from core_agents.central_hub_agent import _strategy_relay_kind

        assert _strategy_relay_kind("#### 1. Verdict\naccept") == "completed"
        assert _strategy_relay_kind("Job ID: x\nEstimated Time: 50 seconds") == "pending"
        assert _strategy_relay_kind("Supported operators: crosses_above, less_than.") == "other"

    def test_relay_marker_preserves_unrecognized_output_verbatim(self) -> None:
        """An unlabeled shape is still wrapped and left byte-for-byte intact."""
        from core_agents.central_hub_agent import (
            _strategy_relay_kind,
            _wrap_terminal_strategy_output,
        )

        payload = "Status: completed  \nJob ID: bt_a707b0de  \n\n### Fold results (train)"
        wrapped = _wrap_terminal_strategy_output(payload, _strategy_relay_kind(payload))

        assert wrapped.startswith("__TERMINAL_TOOL_OUTPUT__:strategy_analysis:other")
        assert wrapped.endswith(payload)
