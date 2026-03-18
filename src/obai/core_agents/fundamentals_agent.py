"""Fundamentals Agent for company financials, ratios, and analyst estimates.

This agent specializes in:
    - Company profiles (sector, industry, market cap)
    - Financial statements (income, balance sheet, cash flow)
    - Financial ratios (P/E, ROE, debt ratios, profitability)
    - Analyst estimates and price targets
    - SEC filings (10-K, 10-Q, 8-K regulatory documents)
    - Insider trading activity (executive buys/sells, sentiment signals)
    - Revenue segments (breakdown by product/business line)
    - Explaining financial concepts using educational resources
"""

from .base_agent import BaseAgent


class FundamentalsAgent(BaseAgent):
    """Fundamentals analysis specialist agent with MCP tool integration.

    This agent connects to the fundamentals-server MCP server and provides
    access to company financials, ratios, estimates, and educational resources.

    Example:
        ```python
        async with FundamentalsAgent() as agent:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(agent.agent, "What are AAPL's latest financials?")
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "fundamentals"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_fundamentals_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for company fundamentals: financial statements (income, "
            "balance sheet, cash flow), key metrics, ratios (P/E, ROE, debt), "
            "analyst estimates, price targets, company profiles, SEC filings "
            "(10-K, 10-Q, 8-K), insider trading activity, and revenue segments. "
            "Use for valuation analysis, earnings, financial health, regulatory "
            "filings research, insider sentiment, and business diversification."
        )


async def create_fundamentals_agent() -> FundamentalsAgent:
    """Create and initialize a Fundamentals Agent.

    Returns:
        Initialized FundamentalsAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_fundamentals_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()  # Don't forget to close!
        ```
    """
    agent = FundamentalsAgent()
    await agent.initialize()
    return agent
