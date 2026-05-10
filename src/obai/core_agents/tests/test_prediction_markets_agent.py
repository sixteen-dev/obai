"""Unit tests for Prediction Markets Agent.

Tests agent initialization, tool loading, and basic functionality.
"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_agents.mcp import MCPClientError
from core_agents.prediction_markets_agent import (
    PredictionMarketsAgent,
    create_prediction_markets_agent,
)


@pytest.fixture(autouse=True)
def mock_env_vars() -> None:  # type: ignore[misc]
    """Set required environment variables for all tests."""
    os.environ["OPENAI_API_KEY"] = "test-key"
    yield


class TestPredictionMarketsAgentInitialization:
    """Test Prediction Markets Agent initialization."""

    @staticmethod
    def _read_prompt_file(name: str) -> str:
        """Read prompt markdown directly from the repository."""
        prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
        return (prompts_dir / f"{name}.md").read_text()

    @staticmethod
    def _read_hub_skill(name: str) -> str:
        """Read hub skill markdown directly from the repository."""
        skills_dir = Path(__file__).resolve().parents[1] / "hub_skills"
        return (skills_dir / name / "SKILL.md").read_text()

    def test_agent_creation(self) -> None:
        """Test agent can be created without errors."""
        agent = PredictionMarketsAgent()
        assert agent is not None
        assert not agent._initialized
        assert agent.mcp_client is None
        assert agent.tool_converter is None
        assert agent.agent is None

    def test_agent_properties(self) -> None:
        """Test agent type properties are correct."""
        agent = PredictionMarketsAgent()
        assert agent.agent_type == "prediction_markets"
        assert agent.mcp_url_property == "mcp_prediction_markets_url"
        assert agent.sdk_agent_name == "obai_prediction_markets_agent"

    def test_handoff_description_mentions_polymarket(self) -> None:
        """Test handoff description includes Polymarket context."""
        agent = PredictionMarketsAgent()
        desc = agent.handoff_description
        assert "Polymarket" in desc
        assert "prediction market" in desc

    def test_prediction_markets_prompt_has_response_modes(self) -> None:
        """Prediction-market prompt should require intent-matched synthesis."""
        prompt = self._read_prompt_file("prediction_markets")
        assert "## Response modes" in prompt
        assert "Match the output to the user's request" in prompt

    def test_prediction_markets_prompt_requires_explicit_trade_decision(self) -> None:
        """Trade recommendations should end with a direct signal."""
        prompt = self._read_prompt_file("prediction_markets")
        assert "end with one explicit decision" in prompt
        assert "Buy YES, Buy NO, or No trade" in prompt

    def test_prediction_markets_prompt_has_edge_framework(self) -> None:
        """Trade recommendations should require fair-value and edge estimation."""
        prompt = self._read_prompt_file("prediction_markets")
        assert "fair-value" in prompt.lower() or "fair value" in prompt.lower()
        assert "edge" in prompt.lower()
        assert "If these conditions are not met, output No trade" in prompt

    def test_prediction_markets_prompt_forbids_invented_market_urls(self) -> None:
        """Prediction-market prompt should prevent synthesized slugs or URLs."""
        prompt = self._read_prompt_file("prediction_markets")
        assert "Construct or guess a Polymarket slug or URL" in prompt
        assert "Only show `slug` or `market_url` values that came from tool data" in prompt
        assert "no relevant active market was found" in prompt

    def test_prediction_markets_prompt_prefers_market_url_over_slug(self) -> None:
        """Prediction-market prompt should include URLs before slug fallback."""
        prompt = self._read_prompt_file("prediction_markets")
        assert "include the tool-provided `market_url`" in prompt
        assert "Use `slug` only as a fallback when `market_url` is absent" in prompt

    def test_hub_prediction_market_skill_has_terminal_relay_rules(self) -> None:
        """Hub skill should preserve prediction-market terminal output."""
        skill = self._read_hub_skill("obai-prediction-market-routing")
        assert "`prediction_market_analysis` is a terminal author" in skill
        assert "__TERMINAL_TOOL_OUTPUT__:prediction_market_analysis:" in skill
        assert "do not write a summary, framing, or wrapper text" in skill
        assert "The runtime emits the specialist's output to the user directly" in skill

    def test_hub_prediction_market_skill_preserves_routing_identifiers(self) -> None:
        """Hub skill should preserve Polymarket routing keys without exposing raw IDs."""
        skill = self._read_hub_skill("obai-prediction-market-routing")
        assert "preserve any tool-provided routing keys" in skill
        assert "`market_url`, `slug`, `condition_id`, `token_id`" in skill
        assert "`condition_id` and `token_id` are kept internal" in skill
        assert "do not invent market URLs, slugs, prices, odds, or liquidity figures" in skill

    @pytest.mark.asyncio
    async def test_agent_initialize_without_mcp_server(self) -> None:
        """Test agent initialization fails gracefully when MCP server is down."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(side_effect=MCPClientError("Connection refused"))

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            agent = PredictionMarketsAgent()

            with pytest.raises(MCPClientError):
                await agent.initialize()

            assert agent.mcp_client is None
            assert not agent._initialized
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_double_initialize(self) -> None:
        """Test that double initialization is handled gracefully."""
        agent = PredictionMarketsAgent()

        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            await agent.initialize()
            assert agent._initialized

            await agent.initialize()
            assert agent._initialized

    @pytest.mark.asyncio
    async def test_agent_close(self) -> None:
        """Test agent cleanup."""
        agent = PredictionMarketsAgent()
        mock_client = AsyncMock()
        agent.mcp_client = mock_client
        agent._initialized = True

        await agent.close()

        mock_client.close.assert_called_once()
        assert agent.mcp_client is None
        assert not agent._initialized


class TestPredictionMarketsAgentToolLoading:
    """Test Prediction Markets Agent tool loading."""

    @pytest.mark.asyncio
    async def test_tool_loading_success(self) -> None:
        """Test successful tool loading from MCP server."""
        agent = PredictionMarketsAgent()

        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()

            async def mock_tool_func(**kwargs):  # type: ignore[no-untyped-def]
                return {}

            mock_tool_func.__name__ = "search_prediction_markets"
            mock_converter.load_tools = AsyncMock(return_value=[mock_tool_func])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            mock_agent_instance = MagicMock()
            mock_agent_class.return_value = mock_agent_instance

            await agent.initialize()

            mock_converter.load_tools.assert_called_once()

            mock_agent_class.assert_called_once()
            call_kwargs = mock_agent_class.call_args.kwargs
            assert "tools" in call_kwargs
            assert len(call_kwargs["tools"]) == 1

    @pytest.mark.asyncio
    async def test_tool_loading_failure(self) -> None:
        """Test handling of tool loading failure."""
        agent = PredictionMarketsAgent()

        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(side_effect=MCPClientError("Connection failed"))

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            with pytest.raises(MCPClientError):
                await agent.initialize()

            assert agent.mcp_client is None
            assert not agent._initialized


class TestPredictionMarketsAgentContextManager:
    """Test Prediction Markets Agent context manager."""

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test agent works as async context manager."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            async with PredictionMarketsAgent() as agent:
                assert agent._initialized
                assert agent.agent is not None

            mock_client.close.assert_called_once()


class TestPredictionMarketsAgentConvenienceFunction:
    """Test convenience function for agent creation."""

    @pytest.mark.asyncio
    async def test_create_prediction_markets_agent(self) -> None:
        """Test create_prediction_markets_agent helper function."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            agent = await create_prediction_markets_agent()

            assert agent is not None
            assert agent._initialized
            assert agent.agent is not None

            await agent.close()
