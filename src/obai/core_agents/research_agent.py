"""Research Agent for deep company research via Exa semantic search.

This agent specializes in:
    - Company profiles and business model deep dives
    - CEO/exec leadership assessment and track records
    - Product sentiment from user reviews, forums, and app stores
    - Competitive landscape and market share analysis
    - Open-ended research queries beyond standard news/filings
"""

from .base_agent import BaseAgent


class ResearchAgent(BaseAgent):
    """Deep company research specialist agent with MCP tool integration.

    Connects to the research-server MCP server (Exa-powered) and provides
    access to semantic web search for trading-relevant intelligence that
    isn't available through standard news or financial data feeds.

    NOT for breaking news (use EventsNews), SEC filings or insider data
    (use Fundamentals), or live market data (use MarketData).

    Example:
        ```python
        async with ResearchAgent() as agent:
            from agents import Runner
            result = await Runner.run(
                agent.agent,
                "What's the full picture on Palantir's business and competitive position?"
            )
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "research"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_research_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for deep company and thematic research beyond news. "
            "Use for qualitative synthesis across web sources, including "
            "business model analysis, leadership assessment, product reception, "
            "competitive dynamics, industry structure, and long-horizon "
            "bull/bear framing. Not for breaking news, earnings data, "
            "filings, insider activity, or live market data."
        )


async def create_research_agent() -> ResearchAgent:
    """Create and initialize a Research Agent.

    Returns:
        Initialized ResearchAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    """
    agent = ResearchAgent()
    await agent.initialize()
    return agent
