"""Unit tests for BaseAgent class.

Tests the abstract base class functionality used by all specialist agents.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_agents.base_agent import BaseAgent
from core_agents.config import reset_config
from core_agents.mcp import MCPClientError


# Concrete implementation for testing (prefix with _ to avoid pytest collection)
class _MockAgent(BaseAgent):
    """Concrete implementation of BaseAgent for testing."""

    @property
    def agent_type(self) -> str:
        return "mock"

    @property
    def mcp_url_property(self) -> str:
        return "mcp_market_data_url"  # Use existing config property

    @property
    def handoff_description(self) -> str:
        return "Mock agent for testing handoffs"


@pytest.fixture(autouse=True)
def setup_env() -> None:  # type: ignore[misc]
    """Set required environment variables and reset config for all tests."""
    os.environ["OPENAI_API_KEY"] = "test-key"
    reset_config()  # Reset config between tests
    yield
    reset_config()  # Clean up after test


class TestBaseAgentProperties:
    """Test BaseAgent property methods."""

    def test_agent_name(self) -> None:
        """Test agent_name property generates correct human-readable name."""
        agent = _MockAgent()
        # "mock" -> "OBaI Mock Agent"
        assert "OBaI" in agent.agent_name
        assert "Mock" in agent.agent_name

    def test_sdk_agent_name(self) -> None:
        """Test sdk_agent_name property generates correct SDK name."""
        agent = _MockAgent()
        assert agent.sdk_agent_name == "obai_mock_agent"

    def test_get_mcp_url(self) -> None:
        """Test _get_mcp_url retrieves correct URL from config."""
        agent = _MockAgent()
        url = agent._get_mcp_url()
        # Should get from mcp_market_data_url config
        assert "localhost" in url or "http" in url


class TestBaseAgentLifecycle:
    """Test BaseAgent initialization and cleanup lifecycle."""

    @pytest.mark.asyncio
    async def test_initialize_success(self) -> None:
        """Test successful initialization flow."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
            patch("core_agents.base_agent.load_prompt") as mock_load_prompt,
        ):
            # Setup mocks
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])
            mock_load_prompt.return_value = "Test instructions"

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            agent = _MockAgent()
            assert not agent._initialized

            await agent.initialize()

            assert agent._initialized
            assert agent.mcp_client is not None
            assert agent.tool_converter is not None
            assert agent.agent is not None

    @pytest.mark.asyncio
    async def test_cleanup_on_init_failure(self) -> None:
        """Test that resources are cleaned up when initialization fails."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(side_effect=MCPClientError("Failed"))

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter

            agent = _MockAgent()

            with pytest.raises(MCPClientError):
                await agent.initialize()

            # Resources should be cleaned up
            assert agent.mcp_client is None
            assert agent.tool_converter is None
            assert agent.agent is None
            assert not agent._initialized
            # Should have called close on the client
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_clears_all_resources(self) -> None:
        """Test that close properly clears all resources."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
            patch("core_agents.base_agent.load_prompt") as mock_load_prompt,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])
            mock_load_prompt.return_value = "Test instructions"

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            agent = _MockAgent()
            await agent.initialize()
            assert agent._initialized

            await agent.close()

            # Everything should be None
            assert agent.mcp_client is None
            assert agent.tool_converter is None
            assert agent.agent is None
            assert not agent._initialized

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        """Test async context manager protocol."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
            patch("core_agents.base_agent.load_prompt") as mock_load_prompt,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])
            mock_load_prompt.return_value = "Test instructions"

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            async with _MockAgent() as agent:
                assert agent._initialized
                assert agent.agent is not None

            # Should have cleaned up after exiting context
            mock_client.close.assert_called_once()


class TestBaseAgentRepr:
    """Test BaseAgent string representation."""

    def test_repr_not_initialized(self) -> None:
        """Test repr shows not initialized status."""
        agent = _MockAgent()
        repr_str = repr(agent)
        assert "_MockAgent" in repr_str
        assert "not initialized" in repr_str

    @pytest.mark.asyncio
    async def test_repr_initialized(self) -> None:
        """Test repr shows initialized status."""
        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
            patch("core_agents.base_agent.load_prompt") as mock_load_prompt,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()
            mock_converter.load_tools = AsyncMock(return_value=[])
            mock_load_prompt.return_value = "Test instructions"

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            agent = _MockAgent()
            await agent.initialize()

            repr_str = repr(agent)
            assert "_MockAgent" in repr_str
            assert "initialized" in repr_str
            assert "not initialized" not in repr_str

            await agent.close()
