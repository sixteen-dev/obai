"""Prediction Markets Agent for venue-aware prediction-market analysis.

This agent specializes in:
    - Prediction market discovery and understanding
    - Executable market state (bid/ask/spread/depth)
    - Market comparison and trade thesis generation
    - Trade flow and holder analysis
    - Trader leaderboard and wallet tracing
    - Setup-based backtesting over resolved markets

It produces human-readable trade memos, not machine-consumable signals.
"""

from .base_agent import BaseAgent


class PredictionMarketsAgent(BaseAgent):
    """Prediction-market specialist agent with MCP tool integration.

    Connects to the prediction-markets-server MCP server and provides
    access to market discovery, pricing, trade flow, wallet analysis,
    and setup-based backtesting. The current implementation supports
    Polymarket only, but the agent boundary is prediction markets, not
    a single venue forever.

    NOT for equity strategies (use Strategy), equity prices (use MarketData),
    or general news (use EventsNews).

    Example:
        ```python
        async with PredictionMarketsAgent() as agent:
            from agents import Runner
            result = await Runner.run(
                agent.agent,
                "What is Polymarket pricing for the next Fed rate cut?"
            )
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "prediction_markets"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_prediction_markets_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for prediction market analysis, currently backed by Polymarket. "
            "Use for market discovery, understanding, and comparison; "
            "executable pricing with bid/ask/spread/depth; "
            "trade flow and holder analysis; "
            "trader leaderboard and wallet tracing; "
            "manual trade thesis generation; "
            "and setup-based backtesting over resolved markets. "
            "Not for equity strategies, stock prices, options, or news."
        )


async def create_prediction_markets_agent() -> PredictionMarketsAgent:
    """Create and initialize a Prediction Markets Agent.

    Returns:
        Initialized PredictionMarketsAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    """
    agent = PredictionMarketsAgent()
    await agent.initialize()
    return agent
