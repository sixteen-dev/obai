"""Screener Agent for stock screening and ticker discovery.

This agent specializes in:
    - Stock screening with various filters (market cap, sector, price, volume)
    - Company name to ticker resolution
    - Ticker symbol validation and discovery
    - Finding stocks that match specific criteria
"""

from .base_agent import BaseAgent


class ScreenerAgent(BaseAgent):
    """Stock screener specialist agent with MCP tool integration.

    This agent connects to the screening-server MCP server and provides
    access to stock screening, company search, and ticker resolution.

    Example:
        ```python
        async with ScreenerAgent() as agent:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(
                agent.agent,
                "Find tech stocks under $50B market cap"
            )
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "screener"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_screener_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for stock screening and discovery: find stocks by market cap, "
            "sector, industry, price, volume, or beta filters. Also resolves company "
            "names to ticker symbols and validates ticker symbols. Use when users want "
            "to discover stocks without knowing specific symbols, or need to find "
            "a company's ticker."
        )


async def create_screener_agent() -> ScreenerAgent:
    """Create and initialize a Screener Agent.

    Returns:
        Initialized ScreenerAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_screener_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()  # Don't forget to close!
        ```
    """
    agent = ScreenerAgent()
    await agent.initialize()
    return agent
