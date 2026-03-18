"""Events/News Agent for news, earnings calendar, and dividend schedules.

This agent specializes in:
    - Company-specific news and press releases
    - Earnings calendar and surprise analysis
    - Dividend schedules and ex-dividend dates
    - Correlating news to price movements
"""

from .base_agent import BaseAgent


class EventsNewsAgent(BaseAgent):
    """Events and news specialist agent with MCP tool integration.

    This agent connects to the events-news-server MCP server and provides
    access to company news, earnings calendar, and dividend schedules.

    Example:
        ```python
        async with EventsNewsAgent() as agent:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(agent.agent, "What news came out about AAPL?")
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "events_news"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_events_news_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for company news, earnings calendar, and dividend schedules. "
            "Use for recent news about a company, upcoming earnings dates, "
            "dividend ex-dates, and correlating news to stock price movements."
        )


async def create_events_news_agent() -> EventsNewsAgent:
    """Create and initialize an Events/News Agent.

    Returns:
        Initialized EventsNewsAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_events_news_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()  # Don't forget to close!
        ```
    """
    agent = EventsNewsAgent()
    await agent.initialize()
    return agent
