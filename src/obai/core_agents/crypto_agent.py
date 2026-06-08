"""Crypto Agent for Coinbase spot research, backtesting, and artifacts."""

from .base_agent import BaseAgent


class CryptoAgent(BaseAgent):
    """Coinbase spot crypto specialist with MCP tool integration.

    This agent connects to the crypto-server MCP server. V1 is Coinbase
    Advanced Trade public market data only, plus an internal Coinbase paper
    ledger for paper-simulation artifacts.
    """

    @property
    def agent_type(self) -> str:
        """Agent type for config and prompt lookup."""
        return "crypto"

    @property
    def mcp_url_property(self) -> str:
        """Config property for MCP server URL."""
        return "mcp_crypto_url"

    @property
    def handoff_description(self) -> str:
        """Description for orchestrator handoff decisions."""
        return (
            "Specialist for Coinbase spot crypto market data, OHLCV retrieval, "
            "order book snapshots, latest trade and quote checks, Coinbase-only "
            "spot strategy backtests, and internal Coinbase paper-ledger strategy "
            "artifacts. Not for equities, options, Polymarket, DeFi research, "
            "derivatives, funding rates, open interest, or live order placement."
        )

    def _get_model(self) -> str:
        """Get model for crypto agent."""
        return self.config.get_agent_model(self.agent_type)


async def create_crypto_agent() -> CryptoAgent:
    """Create and initialize a Crypto Agent."""
    agent = CryptoAgent()
    await agent.initialize()
    return agent
