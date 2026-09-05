"""Unit tests for strategy agent integration.

Tests prompt loading, agent properties, config fields, and hub routing.
Does NOT require live MCP servers.
"""

import importlib.util
import os
import re
import sys
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


_REPO_ROOT = Path(__file__).resolve().parents[4]
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"
_SKILL_PATH = _REPO_ROOT / "skills" / "obai-strategy" / "SKILL.md"
_REFERENCE_PATH = _REPO_ROOT / "skills" / "obai-strategy" / "reference.md"
_CATALOG_PATH = _REPO_ROOT / "src" / "backtest-server" / "src" / "models" / "indicator_catalog.py"

# Placeholder tokens the JSON template must carry for every risk-management and
# position-sizing field the backtest server serializes.
_RISK_TEMPLATE_FIELDS = (
    '"method": "<equal_weight_or_fixed_pct_or_atr_risk>"',
    '"risk_pct": "<number_or_null>"',
    '"atr_indicator": "<atr_indicator_id_or_null>"',
    '"stop_atr_multiple": "<number_or_null>"',
    '"trailing_stop_pct": "<number_or_null>"',
    '"trailing_stop_atr_multiple": "<number_or_null>"',
    '"max_holding_bars": "<integer_or_null>"',
    '"reentry_cooldown_bars": "<integer_or_null>"',
)

# Indicator types registered after the capability roadmap's Stage 1, 2 and 4.
_NEW_INDICATOR_TYPES = (
    "`NATR`",
    "`KAMA`",
    "`PLUS_DI`",
    "`MINUS_DI`",
    "`MAX`",
    "`MIN`",
    "`DONCHIAN`",
    "`ZSCORE`",
    "`RVOL`",
    "`PERCENTILE_RANK`",
    "`KELTNER`",
    "`LAG`",
    "`RATIO`",
    "`DIFF`",
    "`AVWAP`",
    "`OPENING_RANGE`",
)

_UPPERCASE_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_]*)`")


def _read_prompt() -> str:
    """Read the strategy prompt markdown from disk.

    Reads the file rather than ``load_prompt`` so the pins stay deterministic
    when a local Opik server is serving a previously synced prompt version.

    Returns:
        str: The on-disk prompt text.
    """
    return _PROMPT_PATH.read_text()


def _read_skill() -> str:
    """Read the standalone obai-strategy skill body.

    Returns:
        str: The on-disk SKILL.md text.
    """
    return _SKILL_PATH.read_text()


def _read_reference() -> str:
    """Read the standalone obai-strategy schema reference.

    Returns:
        str: The on-disk reference.md text.
    """
    return _REFERENCE_PATH.read_text()


def _prompt_section(text: str, start_marker: str, end_marker: str) -> str:
    """Slice the text between two markers.

    Args:
        text: Markdown to slice.
        start_marker: Literal that opens the slice.
        end_marker: Literal that closes it, searched after the start.

    Returns:
        str: The slice, including the start marker.
    """
    start = text.index(start_marker)
    return text[start : text.index(end_marker, start)]


def _reporting_rule(prompt: str) -> str:
    """Return the single walk-forward reporting bullet.

    Args:
        prompt: The strategy prompt text.

    Returns:
        str: The one line that opens the reporting rule.
    """
    lines = [line for line in prompt.splitlines() if line.startswith("- **Reporting**: Include")]
    assert len(lines) == 1, lines
    return lines[0]


def _reference_section(name: str) -> str:
    """Slice one top-level section out of reference.md.

    Args:
        name: Heading text without the leading hashes.

    Returns:
        str: The section body, up to the next top-level heading.
    """
    text = _read_reference()
    start = text.index(f"## {name}")
    return text[start : text.index("\n## ", start + 1)]


def _supported_indicator_types() -> frozenset[str]:
    """Load the indicator type names the backtest server registers.

    The catalog module is standard-library only by design, so it loads from its
    path without the backtest server's own dependencies.

    Returns:
        frozenset[str]: Every registered indicator type name.
    """
    spec = importlib.util.spec_from_file_location("obai_indicator_catalog", _CATALOG_PATH)
    assert spec is not None and spec.loader is not None, _CATALOG_PATH
    module = importlib.util.module_from_spec(spec)
    # `dataclasses` resolves annotations through `sys.modules`, so the module has
    # to be registered before it executes, and removed once it has.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        return frozenset(module.INDICATOR_CATALOG)
    finally:
        sys.modules.pop(spec.name, None)


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

    def test_unsupported_claims_must_be_checked_against_the_registry(self) -> None:
        """A wrong "unsupported" call silently backtests less than was asked.

        Asked for a volatility-regime filter, the agent declared return-based
        realized volatility unrepresentable, dropped the gate, and tested a
        trend-only proxy. The engine supports it: indicators compute in order
        into one frame, so `source` reaches an earlier indicator's column and
        a statistic of returns is a chain. The capability was real, undisclosed,
        and guessed at from memory.

        Reads the markdown directly, as the guards above do.
        """
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"
        prompt = prompt_path.read_text()

        assert "Before listing anything as unsupported" in prompt
        assert "backtest_get_supported_indicators_tool" in prompt
        assert "`source_note`" in prompt
        # The composition rule has to be stated where indicators are described,
        # not only where the claim is made, so it is present while building too.
        assert "the `id` of any indicator declared before it" in prompt

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

    def test_walk_forward_reporting_covers_each_folds_warnings(self) -> None:
        """The reporting rule must send the agent to per-fold `warnings`.

        Fold metrics now carry the window's quality report. A fold that ran on
        materially insufficient data still produces a consistency score, so the
        warnings have to be read and surfaced under the existing data-warning
        rule rather than left in the payload.

        Reads the markdown directly for the same reason as the guard above.
        """
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"
        prompt = prompt_path.read_text()

        reporting = [
            line for line in prompt.splitlines() if line.startswith("- **Reporting**: Include")
        ]
        assert len(reporting) == 1, reporting
        assert "`warnings`" in reporting[0]
        assert "Data warnings" in reporting[0]

    def test_total_return_claim_is_scoped_to_daily_backtests(self) -> None:
        """Only daily bars are dividend-adjusted; intraday bars are raw.

        `price_basis_for` in the backtest server stores daily bars on a
        dividend-adjusted basis and intraday bars unadjusted, so an unqualified
        total-return claim overstates intraday results.

        Reads the markdown directly for the same reason as the guard above.
        """
        prompt = _read_prompt()

        assert "Daily backtests report total returns" in prompt
        assert "All reported returns are total returns" not in prompt
        assert "Intraday timeframes run on unadjusted prices" not in prompt, (
            "the result now carries `price_basis`, so the basis is read off the run "
            "instead of being asserted per timeframe"
        )

    def test_turnover_rate_is_described_as_traded_notional(self) -> None:
        """`turnover_rate` is traded notional over mean equity, not P&L.

        The portfolio metric sums entry and exit notional; the old wording
        described absolute trade P&L, which reads as a profitability measure.

        Reads the markdown directly for the same reason as the guard above.
        """
        prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "strategy.md"
        prompt = prompt_path.read_text()

        assert "total traded notional (entry plus exit) divided by mean equity" in prompt
        assert "sum of absolute trade P&L" not in prompt

    def test_hypothesis_step_names_capabilities_and_failure_regime(self) -> None:
        """A hypothesis has to declare its failure regime and its capabilities.

        "Form a specific hypothesis." left the agent free to propose mechanics
        the engine cannot run and to skip the falsification question entirely.
        The step now names both, and points the capability check at the
        discovery tool instead of memory.
        """
        prompt = _read_prompt()
        skill = _read_skill()

        for text, label in ((prompt, "strategy.md"), (skill, "SKILL.md")):
            assert "the regime in which it should fail" in text, label
            assert "the engine capabilities it needs" in text, label

    def test_backtest_evidence_names_exposure_turnover_drawdown_dates_costs_and_stability(
        self,
    ) -> None:
        """Headline ratios alone hide how a result was produced.

        Regime concentration, time in market, churn, the drawdown window, the
        cost basis and the parameter neighbourhood are all reportable from
        fields the server already emits, so the evidence contract names them.
        """
        evidence = _prompt_section(
            _read_prompt(), "#### 3. Backtest Evidence", "#### 4. Iteration Summary"
        )

        for field_name in (
            "`yearly_returns`",
            "`capital_utilization_pct`",
            "`turnover_rate`",
            "`max_drawdown_start`",
            "`max_drawdown_end`",
            "`fill_model`",
            "Parameter stability",
        ):
            assert field_name in evidence, field_name

    def test_evidence_reads_price_basis_and_dependency_versions_from_the_result(self) -> None:
        """Provenance is server-owned, so it is read, not asserted.

        The result now carries `price_basis` and `dependency_versions`; both
        the evidence contract and the walk-forward reporting rule send the
        agent to them rather than to a remembered basis or library version.
        """
        prompt = _read_prompt()
        evidence = _prompt_section(prompt, "#### 3. Backtest Evidence", "#### 4. Iteration Summary")
        reporting = _reporting_rule(prompt)

        for field_name in ("`price_basis`", "`dependency_versions`"):
            assert field_name in evidence, field_name
            assert field_name in reporting, field_name

    def test_reject_is_a_complete_answer(self) -> None:
        """Nothing in the contract may push a weak candidate to promotion.

        The nine-section deliverable expects a recommendation, which reads as
        pressure to promote. `reject` plus the best-tested JSON satisfies it.
        """
        prompt = _read_prompt()
        skill = _read_skill()

        for text, label in ((prompt, "strategy.md"), (skill, "SKILL.md")):
            assert "`reject` with the best-tested JSON is a complete answer" in text, label

    def test_benchmark_overlap_is_allowed_for_timing_overlays(self) -> None:
        """Buy-and-hold of the traded asset is the right timing comparison.

        The old prohibition forbade the only meaningful benchmark for a timing
        overlay on one asset, and contradicted the server, which deliberately
        reuses universe data for an overlapping benchmark.
        """
        prompt = _read_prompt()
        skill = _read_skill()

        for text, label in ((prompt, "strategy.md"), (skill, "SKILL.md")):
            assert "Never benchmark a strategy against a symbol it already trades" not in text, (
                label
            )
            assert "buy-and-hold of that asset" in text, label

    def test_trade_count_is_not_presented_as_significance(self) -> None:
        """Consecutive trades in one symbol overlap, so a count is not power.

        A count floor presented as a significance test invites reporting the
        threshold as met instead of stating the power limitation.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "100+" not in text, label
            assert "not a significance test" in text, label

    def test_zero_trades_route_to_signal_diagnostics(self) -> None:
        """Zero trades has several causes with different fixes.

        `signal_diagnostics` separates an unprimed indicator, a predicate that
        never fires and a signal that fires but is never filled, so the rule
        reads it before any threshold moves.
        """
        prompt = _read_prompt()
        skill = _read_skill()

        for text, label in ((prompt, "strategy.md"), (skill, "SKILL.md")):
            assert "`signal_diagnostics`" in text, label
            assert "the entry conditions are broken" not in text, label

    def test_walk_forward_reporting_names_the_capped_warmup_preroll(self) -> None:
        """A capped pre-roll is now reported, so a fold must surface it.

        The warm-up planner truncates at its bar cap and says so in the fold's
        `warnings`; unstabilized leading bars change how a fold reads.
        """
        reporting = _reporting_rule(_read_prompt())

        assert "warm-up pre-roll was capped" in reporting

    def test_intraday_guidelines_scope_relative_volume_to_preceding_bars(self) -> None:
        """`RVOL` is not a same-time-of-day comparison on intraday bars.

        Its denominator is the preceding bars, so a session-shape effect lands
        in the value and must not be read as unusual participation.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "`RVOL`" in text, label
            assert "not the same time of day" in text, label

    def test_signals_skipped_count_covers_more_than_capital(self) -> None:
        """Cooldown and an undefined ATR also skip a fired entry signal.

        The metric is no longer capital-only, and the per-reason split lives in
        `signal_diagnostics.entries_skipped_by_reason`.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "could not be filled due to capital constraints" not in text, label
            assert "`signal_diagnostics.entries_skipped_by_reason` splits them" in text, label

    def test_tool_realism_no_longer_denies_time_and_trailing_stops(self) -> None:
        """Both mechanics now exist, so listing them as unsupported is wrong.

        `max_holding_bars` and the trailing stop ship in `risk_management`; the
        old bullets would make the agent drop a rule the engine can run.
        """
        prompt = _read_prompt()
        skill = _read_skill()

        for text, label in ((prompt, "strategy.md"), (skill, "SKILL.md")):
            assert "max holding period logic" not in text, label
            assert "dynamic ATR trailing-stop logic" not in text, label

    def test_json_template_carries_every_risk_and_sizing_field(self) -> None:
        """The template is the schema the agent copies its field set from.

        A field absent from it is a field the agent never emits, so every new
        `risk_management` and `position_sizing` key carries a placeholder.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            for placeholder in _RISK_TEMPLATE_FIELDS:
                assert placeholder in text, f"{label}: {placeholder}"

    def test_field_rules_describe_every_new_risk_and_sizing_field(self) -> None:
        """A placeholder without a rule is a field used at random.

        One sentence per field states what it does and what it excludes.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "`atr_indicator` names a declared indicator whose `type` is `ATR`" in text, label
            assert "it is frozen for the trade and excludes `stop_loss_pct`" in text, label
            assert "equals `risk_pct` of equity at the fill" in text, label
            assert "ratchets only upward" in text, label
            assert "counting the entry bar as the first" in text, label
            assert "blocks a new entry in a symbol for that many bars" in text, label

    def test_supported_indicators_name_every_new_family(self) -> None:
        """An unlisted type is a capability the agent reports as unsupported.

        The catalog grew by six natives, eight composites and two session
        anchors; the section names each family with its param names only.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            for indicator_type in _NEW_INDICATOR_TYPES:
                assert indicator_type in text, f"{label}: {indicator_type}"

    def test_forward_source_reference_is_rejected_not_warned(self) -> None:
        """A forward `source` no longer degrades to a warning.

        Validation rejects it, so the prompt must not tell the agent the run
        continues without the indicator.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "is rejected at validation" in text, label
        assert "is reported as a warning" not in prompt

    def test_discovery_tool_metadata_lists_ranges_and_lookback(self) -> None:
        """The tool reports more than names and scales now.

        Accepted ranges, defaults and lookback bars are what keep the agent
        from guessing a parameter the engine then rejects.
        """
        prompt = _read_prompt()
        skill = _read_skill()

        for text, label in ((prompt, "strategy.md"), (skill, "SKILL.md")):
            assert "accepted ranges" in text, label
            assert "lookback" in text, label

    def test_benchmark_close_is_available_as_an_operand_and_a_source(self) -> None:
        """`benchmark_close` is the only cross-series column the engine offers.

        It is a rule operand, an indicator `source`, and a `second_source`, so
        a benchmark-relative measure stops being "unsupported".
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "`benchmark_close`" in text, label
            assert "`universe.benchmark` is set" in text, label

    def test_dual_input_default_second_source_is_described_as_high(self) -> None:
        """The engine defaults `second_source` to the `high` column.

        The prompt claimed it defaults to the indicator's own `source`, which
        would make BETA and CORREL compare a series with itself.
        """
        prompt = _read_prompt()
        reference = _read_reference()

        for text, label in ((prompt, "strategy.md"), (reference, "reference.md")):
            assert "defaults to the indicator's `source`" not in text, label
            assert "default to the `high` column" in text, label
            assert "`RATIO` and `DIFF` have no default" in text, label


class TestStrategySkill:
    """Test the standalone obai-strategy skill files."""

    def test_reference_templates_carry_no_numbers(self) -> None:
        """Templates teach rule shapes, never remembered parameter values.

        A number in a template is a market prior the agent copies instead of
        testing, which is exactly what the prompt-editing rule forbids.
        """
        section = _reference_section("Hypothesis templates")
        headings = [line for line in section.splitlines() if line.startswith("### ")]

        assert len(headings) == 3, headings
        assert re.search(r"\d", section) is None, section
        for block in section.split("### ")[1:]:
            for bullet in (
                "Hypothesis:",
                "Entry shape:",
                "Exit shape:",
                "Should fail when:",
                "Requires:",
            ):
                assert f"- {bullet}" in block, f"{block.splitlines()[0]}: {bullet}"

    def test_reference_templates_name_only_registered_capabilities(self) -> None:
        """A template that names an unregistered type sends the agent nowhere.

        Every backticked uppercase token in a `Requires` bullet is a live
        indicator type, or is explicitly marked as not yet available.
        """
        section = _reference_section("Hypothesis templates")
        supported = _supported_indicator_types()
        requires = [line for line in section.splitlines() if line.strip().startswith("- Requires:")]

        assert len(requires) == 3, requires
        for line in requires:
            for token in _UPPERCASE_TOKEN_RE.findall(line):
                assert token in supported or "not yet available" in line, token

    def test_skill_routes_readers_to_the_hypothesis_templates(self) -> None:
        """The templates only help if the skill sends readers to them."""
        assert "including its hypothesis templates" in _read_skill()


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

    def test_strategy_handoff_fidelity_keeps_a_bracketed_rule(self) -> None:
        """A threshold range in brackets is the rule, not metadata.

        Stripping every trailing bracket meant "RSI is in [30, 40]" and the
        same request carrying a different range normalized to identical text,
        so a materially different strategy passed the gate. A trailing period
        happened to save it; a terse query has none.
        """
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        original = "Backtest AAPL: buy when RSI is in [30, 40]"
        rewritten = "Backtest AAPL: buy when RSI is in [70, 80]"

        assert _get_strategy_handoff_fidelity_error(rewritten, original) is not None

    def test_strategy_handoff_fidelity_rejects_a_dropped_range(self) -> None:
        """Dropping the range outright must not normalize to the same request."""
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        original = "Backtest AAPL: buy when RSI is in [30, 40]"
        stripped = "Backtest AAPL: buy when RSI is in"

        assert _get_strategy_handoff_fidelity_error(stripped, original) is not None

    def test_strategy_handoff_fidelity_ignores_the_marker_after_a_bracketed_rule(
        self,
    ) -> None:
        """Both at once: the rule survives, the correlation tag is still ignored."""
        from core_agents.central_hub_agent import _get_strategy_handoff_fidelity_error

        request = "Backtest AAPL: buy when RSI is in [30, 40]"
        submitted = (
            f"{request}\n\n"
            "[OBaI regression correlation: regress:CORE-STRAT:00317b94. "
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
