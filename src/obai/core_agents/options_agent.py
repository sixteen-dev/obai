"""Options Agent for real-time options chains, Greeks, and derivatives analysis.

This agent specializes in:
    - Real-time options chains with Greeks (via Polygon.io)
    - Individual contract snapshots and quotes
    - Greeks analysis (delta, gamma, theta, vega)
    - Implied volatility and open interest
    - Historical options trades and quotes
"""

from .base_agent import BaseAgent


class OptionsAgent(BaseAgent):
    """Options and derivatives specialist agent with MCP tool integration.

    This agent connects to the options-server MCP server (powered by Polygon.io)
    and provides access to real-time options chains, Greeks, and implied volatility.

    Example:
        ```python
        async with OptionsAgent() as agent:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(
                agent.agent,
                "What are the Greeks for AAPL 190 calls expiring next week?"
            )
        ```
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "options"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_options_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for options and derivatives: real-time options chains, "
            "Greeks (delta, gamma, theta, vega), implied volatility, open interest, "
            "NBBO quotes, and contract snapshots. Use for any options-related queries."
        )


async def create_options_agent() -> OptionsAgent:
    """Create and initialize an Options Agent.

    Returns:
        Initialized OptionsAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.

    Example:
        ```python
        agent = await create_options_agent()
        # Use agent.agent with Agent SDK Runner
        await agent.close()  # Don't forget to close!
        ```
    """
    agent = OptionsAgent()
    await agent.initialize()
    return agent
