"""Unit tests for strategy agent integration.

Tests prompt loading, agent properties, config fields, and hub routing.
Does NOT require live MCP servers.
"""

import os

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

    def test_prompt_lists_supported_indicators(self) -> None:
        """Strategy prompt should list available indicators."""
        prompt = load_prompt("strategy")
        assert "SMA" in prompt
        assert "RSI" in prompt
        assert "MACD" in prompt

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
        config = AgentConfig()  # type: ignore[call-arg]
        assert "localhost:8007" in config.mcp_backtest_url

    def test_strategy_model_default(self) -> None:
        """Strategy model should default to gpt-5.1 (strong reasoning needed)."""
        config = AgentConfig()  # type: ignore[call-arg]
        assert config.strategy_model == "gpt-5.1"

    def test_strategy_model_fallback(self) -> None:
        """Strategy model should fall back to orchestrator_model when None."""
        config = AgentConfig()  # type: ignore[call-arg]
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

    def test_hub_prompt_preserves_user_request_for_strategy(self) -> None:
        """Hub prompt should forbid prescriptive strategy sub-prompts."""
        prompt = load_prompt("central_hub", USER_PREFERENCES="{}")
        assert "Preserve the user's original request faithfully" in prompt
        assert "Do not tell `strategy_analysis` to skip backtesting" in prompt


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

    def test_wrapper_prefixes_execution_note(self) -> None:
        """Wrapper should remind strategy agent to ignore conflicting hub phrasing."""
        from core_agents.central_hub_agent import _prepare_strategy_handoff_input

        prepared = _prepare_strategy_handoff_input(
            "You do NOT need to run a backtest. Design a mean-reversion strategy for AAPL."
        )

        assert "Do not skip required backtesting" in prepared
        assert prepared.endswith(
            "You do NOT need to run a backtest. Design a mean-reversion strategy for AAPL."
        )
