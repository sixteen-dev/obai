"""Unit tests for the Options Strategy Agent (ADR 0003 §2.1, §2.3, §4.3 Phase A).

The agent reads the ADR 0003 §2.2 config fields ``mcp_options_backtest_url``,
``options_strategy_model`` and ``options_strategy_reasoning_effort`` from
``AgentConfig``, so every assertion runs against its real resolution rules.

Prompt pins read ``prompts/options_strategy.md`` directly, never through
``load_prompt``, so a prompt previously synced to a local Opik server cannot
change what they check.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core_agents.config import AgentConfig
from core_agents.mcp import MCPClientError
from core_agents.options_strategy_agent import OptionsStrategyAgent
from core_agents.prompt_loader import validate_prompt

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "options_strategy.md"

_REQUESTED_ACTIONS = ("build", "backtest", "compare", "explain", "status")
_SHORT_FORM_HEADINGS = (
    "**Status**",
    "**Reference**",
    "**Supported next action**",
    "**Explanation**",
)
_STATUS_VALUES = ("`validated`", "`rejected`", "`unavailable`", "`capability`")


def _config(**overrides: Any) -> AgentConfig:
    """Build a config whose model tiers are all distinct and env-independent.

    Args:
        overrides: Field values replacing the defaults below.

    Returns:
        The config; init kwargs outrank the environment and dotenv.
    """
    values: dict[str, Any] = {
        "openai_api_key": "test-key",
        "orchestrator_model": "orchestrator-model",
        "specialist_model": "specialist-model",
        "strategy_model": "strategy-model",
        "specialist_reasoning_effort": "xhigh",
        "strategy_reasoning_effort": "low",
        "options_strategy_model": None,
        "options_strategy_reasoning_effort": None,
    }
    values.update(overrides)
    return AgentConfig(**values)


def _agent(config: AgentConfig) -> OptionsStrategyAgent:
    """Construct the agent against ``config`` instead of the process config.

    Args:
        config: Config the agent reads.

    Returns:
        An uninitialized agent.
    """
    with patch("core_agents.base_agent.get_config", return_value=config):
        return OptionsStrategyAgent()


def _read_prompt() -> str:
    """Read the options strategy prompt from disk.

    Returns:
        The on-disk prompt text.
    """
    return _PROMPT_PATH.read_text()


class TestOptionsStrategyAgentIdentity:
    """Names and routing description (ADR 0003 §2.1)."""

    def test_agent_type_url_property_and_sdk_name(self) -> None:
        agent = _agent(_config())
        assert agent.agent_type == "options_strategy"
        assert agent.mcp_url_property == "mcp_options_backtest_url"
        assert agent.sdk_agent_name == "obai_options_strategy_agent"
        assert agent._get_mcp_url() == "http://localhost:8012/mcp"

    def test_handoff_description_names_historical_and_managed_rules_scope(self) -> None:
        desc = _agent(_config()).handoff_description
        assert "historical options-strategy validation and backtesting" in desc
        for token in ("SPXW", "XSP", "verticals", "condors", "straddles", "strangles"):
            assert token in desc
        for rule in ("covered call", "cash-secured put", "wheel", "roll"):
            assert rule in desc
        assert "answers with the supported scope" in desc

    def test_handoff_description_excludes_current_market_and_equity_strategies(self) -> None:
        desc = _agent(_config()).handoff_description
        assert "Not for current chains, Greeks, IV, NBBO" in desc
        assert "(options_analysis)" in desc
        assert "not for equity or ETF share strategies (strategy_analysis)" in desc


class TestOptionsStrategyModelResolution:
    """Strategy-class model and effort by construction (ADR 0003 §2.1 as amended)."""

    def test_model_override_wins(self) -> None:
        agent = _agent(_config(options_strategy_model="options-strategy-model"))
        assert agent._get_model() == "options-strategy-model"

    def test_model_inherits_the_strategy_agents_resolved_model(self) -> None:
        config = _config()
        assert _agent(config)._get_model() == config.get_agent_model("strategy")
        assert _agent(config)._get_model() == "strategy-model"

    def test_model_does_not_copy_the_orchestrator_fallback(self) -> None:
        """With no strategy pin, get_agent_model("strategy") is the specialist tier."""
        config = _config(strategy_model=None)
        assert _agent(config)._get_model() == "specialist-model"
        assert _agent(config)._get_model() != config.get_strategy_model()

    def test_reasoning_effort_override_wins(self) -> None:
        agent = _agent(_config(options_strategy_reasoning_effort="high"))
        assert agent._get_reasoning_effort() == "high"

    def test_reasoning_effort_inherits_the_strategy_agents_effort(self) -> None:
        config = _config()
        assert _agent(config)._get_reasoning_effort() == "low"
        assert _agent(config)._get_reasoning_effort() == config.get_agent_reasoning_effort(
            "strategy"
        )

    def test_model_fails_loud_when_the_override_field_is_missing(self) -> None:
        """A renamed or dropped config field must raise, not fall back silently."""
        agent = _agent(_config())
        agent.config = SimpleNamespace(get_agent_model=lambda _agent_type: "strategy-model")  # type: ignore[assignment]
        with pytest.raises(AttributeError, match="options_strategy_model"):
            agent._get_model()

    def test_reasoning_effort_fails_loud_when_the_override_field_is_missing(self) -> None:
        agent = _agent(_config())
        agent.config = SimpleNamespace(get_agent_reasoning_effort=lambda _agent_type: "low")  # type: ignore[assignment]
        with pytest.raises(AttributeError, match="options_strategy_reasoning_effort"):
            agent._get_reasoning_effort()

    @pytest.mark.asyncio
    async def test_initialize_builds_the_sdk_agent_with_resolved_model_and_effort(self) -> None:
        config = _config()
        agent = _agent(config)
        with (
            patch("core_agents.base_agent.MCPClient") as client_class,
            patch("core_agents.base_agent.MCPToolConverter") as converter_class,
            patch("core_agents.base_agent.load_prompt", return_value="prompt") as load,
        ):
            converter_class.return_value.load_tools = AsyncMock(return_value=[])
            await agent.initialize()

        assert client_class.call_args.kwargs["base_url"] == config.mcp_options_backtest_url
        load.assert_called_once_with("options_strategy")
        assert agent._initialized
        assert agent.agent is not None
        assert agent.agent.name == "obai_options_strategy_agent"
        assert agent.agent.model == "strategy-model"
        assert agent.agent.model_settings.reasoning is not None
        assert agent.agent.model_settings.reasoning.effort == "low"
        assert agent.agent.model_settings.parallel_tool_calls is True

    @pytest.mark.asyncio
    async def test_initialize_against_a_failing_server_cleans_up(self) -> None:
        agent = _agent(_config())
        with (
            patch("core_agents.base_agent.MCPClient") as client_class,
            patch("core_agents.base_agent.MCPToolConverter") as converter_class,
        ):
            client = AsyncMock()
            client_class.return_value = client
            converter_class.return_value.load_tools = AsyncMock(
                side_effect=MCPClientError("Connection refused")
            )
            with pytest.raises(MCPClientError):
                await agent.initialize()

        assert agent.mcp_client is None
        assert agent.tool_converter is None
        assert agent.agent is None
        assert not agent._initialized
        client.close.assert_called_once()


class TestOptionsStrategyPrompt:
    """Prompt content pins (ADR 0003 §2.3, §4.3), read from the file."""

    def test_prompt_passes_the_loader_validation(self) -> None:
        prompt = _read_prompt()
        validate_prompt(prompt, "options_strategy")
        assert prompt.startswith("**TODAY'S DATE: $TODAY_DATE**")
        assert "terminal author" in prompt

    def test_prompt_has_no_inline_examples_or_numbers(self) -> None:
        """No example and no numeric default may steer the document's values."""
        prompt = _read_prompt()
        assert re.search(r"\d", prompt) is None
        assert "example" not in prompt.lower()
        assert "e.g." not in prompt

    def test_prompt_names_every_requested_action(self) -> None:
        prompt = _read_prompt()
        for action in _REQUESTED_ACTIONS:
            assert f"- `{action}`:" in prompt

    def test_prompt_reads_scope_from_capabilities_never_memory(self) -> None:
        prompt = _read_prompt()
        assert "`options_backtest_capabilities_tool` at the start of every request" in prompt
        assert "never from memory" in prompt
        assert "never predict when the capability will arrive" in prompt

    def test_prompt_bounds_revalidation_at_two(self) -> None:
        prompt = _read_prompt()
        assert "re-validate at most twice" in prompt
        assert "fixes one field without changing the user's mechanics" in prompt

    def test_prompt_never_presents_a_staged_rejection_as_every_blocker(self) -> None:
        """Ingestion stops at its first failing stage; the root checks join it (ADR 0003 §8)."""
        prompt = _read_prompt()
        assert (
            "A rejection carries the first failing ingestion stage's issues plus the product "
            "root checks whenever the product block is itself well-formed; the later stages did "
            "not run."
        ) in prompt
        assert "Never present a rejection as every blocker the document has" in prompt

    def test_prompt_compiles_against_the_schema_body_capabilities_return(self) -> None:
        """The specialist cannot write a document against a schema it never saw (ADR 0003 §8)."""
        prompt = _read_prompt()
        assert "the schema body the capabilities payload returns" in prompt
        assert "field names and values the service documents or returns" not in prompt
        assert "leave it out, let validation report it, and name it as an input" in prompt

    def test_prompt_relays_the_hubs_dated_context_in_an_optional_final_section(self) -> None:
        """The current half of a mixed request reaches the user dated (ADR 0003 §8, M5)."""
        prompt = _read_prompt()
        contract = prompt[prompt.index("## Output Guidelines") :]
        section = next(
            line
            for line in contract.splitlines()
            if line.startswith("- **Current market context (dated, from the hub)**")
        )
        assert contract.index("**Explanation**") < contract.index("**Current market context")
        assert "the `Context:` block relayed verbatim with its dates" in section
        assert "Omit this section when the block is blank" in section
        assert "never feeds the document or the validation report" in section
        assert "the one exception is the current market context section" in contract

    def test_prompt_short_form_headings_are_in_order(self) -> None:
        prompt = _read_prompt()
        contract = prompt[prompt.index("## Output Guidelines") :]
        positions = [contract.index(heading) for heading in _SHORT_FORM_HEADINGS]
        assert positions == sorted(positions)
        for status in _STATUS_VALUES:
            assert status in contract

    def test_prompt_forbids_figures_the_service_did_not_return(self) -> None:
        prompt = _read_prompt()
        assert "Never write a performance, drawdown, win-rate or return figure" in prompt
        assert "never a number the service did not return" in prompt
        assert "Never write a seven-section report" in prompt

    def test_prompt_offers_alternatives_and_never_applies_them(self) -> None:
        prompt = _read_prompt()
        assert "Validate an unsupported mechanic as stated" in prompt
        assert "offer a supported alternative as a separate proposal" in prompt
        assert "never apply it to the document" in prompt

    def test_prompt_quotes_the_services_tokens_verbatim(self) -> None:
        """Gate cases assert the codes, pointers and roots the service emits."""
        prompt = _read_prompt()
        assert "quote the issue's `code` and `missing_capability` exactly" in prompt
        assert "`code`, `json_pointer`, `message` and `remediation`, quoted exactly" in prompt
        assert "names every supported option root with its underlying" in prompt
        assert "the product the document carried (its underlying and option roots)" in prompt

    def test_prompt_never_invents_tools_or_drops_issues(self) -> None:
        prompt = _read_prompt()
        never = prompt[prompt.index("## Never") :]
        assert "Invent a tool the service does not list, or call a tool that is absent" in never
        assert "advice about the strategy's merit" in never
        assert "Drop an issue" in never
