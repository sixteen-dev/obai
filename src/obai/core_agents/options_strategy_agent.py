"""Options Strategy Agent for the options backtest service (ADR 0003 §2.1).

This agent compiles a user's options-strategy mechanics into a strategy
document, validates it against the options-backtest-server, and answers with
the short status contract of design §15.5. It never produces a performance
figure: the server exposes capabilities and validation only.
"""

import logging
from dataclasses import replace

from openai.types.shared import Reasoning

from .base_agent import BaseAgent
from .config import ReasoningEffort

logger = logging.getLogger(__name__)


class OptionsStrategyAgent(BaseAgent):
    """Options-strategy specialist backed by the options-backtest-server MCP server.

    Strategy-class by construction: the model and reasoning effort are the
    options-strategy overrides when set, else the strategy agent's resolved
    values through ``get_agent_model("strategy")`` and
    ``get_agent_reasoning_effort("strategy")``. The strategy agent's own
    fallback to ``orchestrator_model`` is not copied.
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "options_strategy"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_options_backtest_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for historical options-strategy validation and backtesting on "
            "US European, PM-settled, cash-settled index options (SPXW, XSP; verticals, "
            "iron condors, straddles, strangles, single long options), and for managed "
            "options rules including covered call, cash-secured put, wheel and roll "
            "requests, which it answers with the supported scope. Not for current chains, "
            "Greeks, IV, NBBO or scenario math on current contracts (options_analysis), "
            "and not for equity or ETF share strategies (strategy_analysis)."
        )

    def _get_model(self) -> str:
        """Get model for the options strategy agent.

        Returns:
            ``options_strategy_model`` when set, else the strategy agent's model
            as ``get_agent_model("strategy")`` resolves it.
        """
        override = self.config.options_strategy_model
        if override is not None:
            return override
        return self.config.get_agent_model("strategy")

    def _get_reasoning_effort(self) -> ReasoningEffort:
        """Get reasoning effort for the options strategy agent.

        Returns:
            ``options_strategy_reasoning_effort`` when set, else the strategy
            agent's effort as ``get_agent_reasoning_effort("strategy")`` resolves it.
        """
        override = self.config.options_strategy_reasoning_effort
        if override is not None:
            return override
        return self.config.get_agent_reasoning_effort("strategy")

    async def initialize(self) -> None:
        """Initialize through ``BaseAgent``, then apply the strategy-class effort.

        ``BaseAgent.initialize`` resolves reasoning effort by ``agent_type``,
        which for this agent would fall back to the specialist tier.

        Raises:
            MCPClientError: If connection to MCP server fails.
            MCPServerError: If MCP server returns error.
            RuntimeError: If the base initialization left no SDK agent.
        """
        await super().initialize()
        if self.agent is None:
            msg = f"{self.agent_name} initialized without an SDK agent"
            raise RuntimeError(msg)
        effort = self._get_reasoning_effort()
        self.agent.model_settings = replace(
            self.agent.model_settings, reasoning=Reasoning(effort=effort)
        )
        logger.info("%s using reasoning effort: %s", self.agent_name, effort)
