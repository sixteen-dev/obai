"""Unit tests for Market Data Agent.

Tests agent initialization, tool loading, and basic functionality.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_agents.market_data_agent import MarketDataAgent, create_market_data_agent
from core_agents.mcp import MCPClientError


@pytest.fixture(autouse=True)
def mock_env_vars() -> None:  # type: ignore[misc]
    """Set required environment variables for all tests."""
    os.environ["OPENAI_API_KEY"] = "test-key"
    yield


class TestMarketDataAgentInitialization:
    """Test Market Data Agent initialization."""

    def test_agent_creation(self) -> None:
        """Test agent can be created without errors."""
        agent = MarketDataAgent()
        assert agent is not None
        assert not agent._initialized
        assert agent.mcp_client is None
        assert agent.tool_converter is None
        assert agent.agent is None

    def test_agent_properties(self) -> None:
        """Test agent type properties are correct."""
        agent = MarketDataAgent()
        assert agent.agent_type == "market_data"
        assert agent.mcp_url_property == "mcp_market_data_url"
        assert agent.sdk_agent_name == "obai_market_data_agent"

    @pytest.mark.asyncio
    async def test_agent_initialize_without_mcp_server(self) -> None:
        """Test agent initialization fails gracefully when MCP server is down."""
        # Mock MCPToolConverter to simulate server failure
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            # Simulate connection failure during tool loading
            mock_converter.load_tools = AsyncMock(side_effect=MCPClientError("Connection refused"))

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            agent = MarketDataAgent()

            # Should raise error if MCP server not available
            with pytest.raises(MCPClientError):
                await agent.initialize()

            # Verify cleanup happened even on failure
            assert agent.mcp_client is None
            assert not agent._initialized
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_double_initialize(self) -> None:
        """Test that double initialization is handled gracefully."""
        agent = MarketDataAgent()

        # Mock the MCP client and tool converter in base_agent where they're imported
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            # Setup mocks
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            # First initialization
            await agent.initialize()
            assert agent._initialized

            # Second initialization should log warning but not fail
            await agent.initialize()
            assert agent._initialized

    @pytest.mark.asyncio
    async def test_agent_close(self) -> None:
        """Test agent cleanup."""
        agent = MarketDataAgent()
        mock_client = AsyncMock()
        agent.mcp_client = mock_client
        agent._initialized = True

        await agent.close()

        # Should have called close on MCP client
        mock_client.close.assert_called_once()
        # After close, mcp_client should be None (cleanup)
        assert agent.mcp_client is None
        assert not agent._initialized


class TestMarketDataAgentToolLoading:
    """Test Market Data Agent tool loading."""

    @pytest.mark.asyncio
    async def test_tool_loading_success(self) -> None:
        """Test successful tool loading from MCP server."""
        agent = MarketDataAgent()

        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            # Setup mocks
            mock_client = AsyncMock()
            mock_converter = AsyncMock()

            # Mock tool converter to return async functions
            async def mock_tool_func(**kwargs):  # type: ignore[no-untyped-def]
                return {}

            mock_tool_func.__name__ = "get_quote"
            mock_converter.load_tools = AsyncMock(return_value=[mock_tool_func])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            mock_agent_instance = MagicMock()
            mock_agent_class.return_value = mock_agent_instance

            await agent.initialize()

            # Verify tool converter was called
            mock_converter.load_tools.assert_called_once()

            # Verify agent was created with tools
            mock_agent_class.assert_called_once()
            call_kwargs = mock_agent_class.call_args.kwargs
            assert "tools" in call_kwargs
            assert len(call_kwargs["tools"]) == 1

    @pytest.mark.asyncio
    async def test_tool_loading_failure(self) -> None:
        """Test handling of tool loading failure."""
        agent = MarketDataAgent()

        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
        ):
            # Setup mocks to raise error
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(side_effect=MCPClientError("Connection failed"))

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            # Should propagate the error
            with pytest.raises(MCPClientError):
                await agent.initialize()

            # Should have cleaned up
            assert agent.mcp_client is None
            assert not agent._initialized


class TestMarketDataAgentContextManager:
    """Test Market Data Agent context manager."""

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test agent works as async context manager."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            # Setup mocks
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            async with MarketDataAgent() as agent:
                assert agent._initialized
                assert agent.agent is not None

            # Should call close after exiting context
            mock_client.close.assert_called_once()


class TestMarketDataAgentConvenienceFunction:
    """Test convenience function for agent creation."""

    @pytest.mark.asyncio
    async def test_create_market_data_agent(self) -> None:
        """Test create_market_data_agent helper function."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            # Setup mocks
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            agent = await create_market_data_agent()

            assert agent is not None
            assert agent._initialized
            assert agent.agent is not None

            # Cleanup
            await agent.close()
