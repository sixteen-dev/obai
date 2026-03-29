"""Strategy Agent for trading strategy design, backtesting, and optimization.

This agent specializes in:
    - Designing technical indicator-based trading strategies
    - Backtesting strategies against historical OHLCV data
    - Computing performance metrics (Sharpe, Sortino, drawdown, alpha/beta)
    - Iteratively refining strategies with train/test discipline
    - Comparing multiple strategy variants for selection
"""

from .base_agent import BaseAgent
from .preferences import PreferencesStore


class StrategyAgent(BaseAgent):
    """Strategy analysis specialist agent with MCP tool integration.

    This agent connects to the backtest-server MCP server and provides
    access to backtesting, indicator computation, and performance metrics.
    Acts as a quantitative analyst that uses market context from the hub
    to make informed strategy design decisions.

    Uses the orchestrator-class model by default (not the specialist model)
    because strategy design requires strong reasoning: iterative backtesting,
    metric interpretation, parameter tuning, and overfitting detection.

    Example:
        ```python
        async with StrategyAgent() as agent:
            from agents import Runner
            result = await Runner.run(
                agent.agent,
                "Design a momentum strategy for AAPL, MSFT, GOOGL"
            )
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "strategy"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_backtest_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for designing trading strategies, backtesting them against "
            "historical data (daily and intraday: 5min, 15min, 1hour), and iteratively "
            "refining based on performance metrics. Supports day trading strategies with "
            "session-aware rules (close_eod, no_entry_after, time-of-day filters). "
            "Acts as a quantitative analyst — uses market context provided by the hub "
            "to make informed strategy decisions. Use for strategy building, "
            "backtesting, or trading system questions."
        )

    def _get_model(self) -> str:
        """Get model for strategy agent.

        Overrides base class to use orchestrator model as default fallback
        instead of specialist model.
        """
        return self.config.get_strategy_model()

    def _get_prompt_kwargs(self) -> dict[str, str]:
        """Inject user's initial_capital into strategy prompt."""
        prefs = PreferencesStore().load()
        return {"INITIAL_CAPITAL": str(int(prefs.initial_capital))}


async def create_strategy_agent() -> StrategyAgent:
    """Create and initialize a Strategy Agent.

    Returns:
        Initialized StrategyAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_strategy_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()
        ```
    """
    agent = StrategyAgent()
    await agent.initialize()
    return agent
