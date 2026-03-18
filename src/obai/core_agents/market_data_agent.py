"""Market Data Agent for real-time quotes, historical prices, and technical analysis.

This agent specializes in:
    - Real-time and historical stock prices
    - Market movers (gainers, losers, most active)
    - Sector performance and market status
    - Technical indicators (RSI, MACD, moving averages)
    - Short interest and volume analysis
"""

from .base_agent import BaseAgent


class MarketDataAgent(BaseAgent):
    """Market data specialist agent with MCP tool integration.

    This agent connects to the market-data-server MCP server and provides
    access to real-time quotes, historical prices, and technical analysis tools.

    Example:
        ```python
        async with MarketDataAgent() as agent:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(agent.agent, "What is AAPL trading at?")
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "market_data"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_market_data_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for real-time stock prices, quotes, historical data, "
            "market movers (gainers/losers), technical indicators (RSI, MACD), "
            "and market status. Use for price checks, charts, and technical analysis."
        )


async def create_market_data_agent() -> MarketDataAgent:
    """Create and initialize a Market Data Agent.

    Returns:
        Initialized MarketDataAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_market_data_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()  # Don't forget to close!
        ```
    """
    agent = MarketDataAgent()
    await agent.initialize()
    return agent
