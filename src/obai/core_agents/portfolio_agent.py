"""Portfolio Agent for portfolio analysis and risk metrics.

This agent specializes in:
    - Parsing portfolio positions from free-form text
    - ETF holdings expansion (look-through analysis)
    - Treasury rates for risk-free rate calculations
    - Portfolio risk metrics (future: Sharpe, beta, VaR)
    - Monte Carlo simulations (future)
"""

from .base_agent import BaseAgent


class PortfolioAgent(BaseAgent):
    """Portfolio analysis specialist agent with MCP tool integration.

    This agent connects to the portfolio-server MCP server and provides
    access to portfolio parsing, preferences, ETF holdings, and risk data.

    Example:
        ```python
        async with PortfolioAgent() as agent:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(agent.agent, "Parse my portfolio: AAPL 40%, QQQ 35%, BND 25%")
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "portfolio"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_portfolio_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for portfolio analysis: parsing portfolio positions from text, "
            "ETF holdings expansion for look-through analysis, Treasury rates for "
            "risk-free rate calculations. "
            "Use for portfolio composition or ETF constituent questions."
        )


async def create_portfolio_agent() -> PortfolioAgent:
    """Create and initialize a Portfolio Agent.

    Returns:
        Initialized PortfolioAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_portfolio_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()  # Don't forget to close!
        ```
    """
    agent = PortfolioAgent()
    await agent.initialize()
    return agent
