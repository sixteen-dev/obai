"""Knowledge Base Agent for corpus lookup (strategies, concepts, vocabulary).

This agent is a thin reference-lookup specialist. It does not analyze, design,
recommend, or improvise. It translates the hub's question into a corpus search
or fetch and returns the canonical entries the hub uses to ground vocabulary
and seed strategy handoffs.

It specializes in:
    - Resolving named trading strategies (e.g., "wheel", "VRP harvest", "spot-perp basis")
    - Looking up market-condition vocabulary (e.g., "contango", "low dispersion", "negative skew")
    - Surfacing candidate strategies for vague universe-flavored queries
    - Returning compact corpus seeds the hub passes to `strategy_analysis`
"""

from .base_agent import BaseAgent


class KnowledgeBaseAgent(BaseAgent):
    """Knowledge-base lookup specialist with MCP tool integration.

    This agent connects to the knowledge-base-server MCP and exposes three
    read-only tools: `kb_search_corpus_tool`, `kb_get_corpus_entry_tool`, and
    `kb_list_categories_tool`. It is intentionally narrow — the corpus is the
    single source of truth and the agent never improvises beyond what the
    corpus returns.
    """

    @property
    def agent_type(self) -> str:
        """Agent type identifier for config and prompt lookup."""
        return "knowledge_base"

    @property
    def mcp_url_property(self) -> str:
        """Config property name for MCP server URL."""
        return "mcp_knowledge_base_url"

    @property
    def handoff_description(self) -> str:
        """Description shown to the orchestrator for routing decisions."""
        return (
            "Looks up named trading strategies and market concepts (regimes, "
            "instruments, factors, mechanics) by name, alias, or natural-language "
            "description. Returns canonical corpus entries the hub uses to ground "
            "vocabulary and seed strategy handoffs. Read-only, librarian-style — "
            "the agent does not analyze or recommend strategies on its own."
        )


async def create_knowledge_base_agent() -> KnowledgeBaseAgent:
    """Create and initialize a Knowledge Base Agent.

    Returns:
        Initialized KnowledgeBaseAgent instance.

    Raises:
        MCPClientError: If connection to MCP server fails.
    """
    agent = KnowledgeBaseAgent()
    await agent.initialize()
    return agent
