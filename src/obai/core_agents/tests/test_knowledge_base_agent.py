"""Unit tests for Knowledge Base Agent.

Tests agent initialization, tool loading, and basic functionality.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_agents.knowledge_base_agent import KnowledgeBaseAgent, create_knowledge_base_agent
from core_agents.mcp import MCPClientError


@pytest.fixture(autouse=True)
def mock_env_vars() -> None:  # type: ignore[misc]
    """Set required environment variables for all tests."""
    os.environ["OPENAI_API_KEY"] = "test-key"
    yield


class TestKnowledgeBaseAgentInitialization:
    """Test Knowledge Base Agent initialization."""

    def test_agent_creation(self) -> None:
        """Test agent can be created without errors."""
        agent = KnowledgeBaseAgent()
        assert agent is not None
        assert not agent._initialized
        assert agent.mcp_client is None
        assert agent.tool_converter is None
        assert agent.agent is None

    def test_agent_properties(self) -> None:
        """Test agent type properties are correct."""
        agent = KnowledgeBaseAgent()
        assert agent.agent_type == "knowledge_base"
        assert agent.mcp_url_property == "mcp_knowledge_base_url"
        assert agent.sdk_agent_name == "obai_knowledge_base_agent"

    def test_handoff_description_present(self) -> None:
        """Handoff description must exist and describe a librarian role."""
        agent = KnowledgeBaseAgent()
        assert agent.handoff_description
        assert "corpus" in agent.handoff_description.lower()

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

            agent = KnowledgeBaseAgent()

            with pytest.raises(MCPClientError):
                await agent.initialize()

            assert agent.mcp_client is None
            assert not agent._initialized
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_close(self) -> None:
        """Test agent cleanup."""
        agent = KnowledgeBaseAgent()
        mock_client = AsyncMock()
        agent.mcp_client = mock_client
        agent._initialized = True

        await agent.close()

        mock_client.close.assert_called_once()
        assert agent.mcp_client is None
        assert not agent._initialized


class TestKnowledgeBaseAgentToolLoading:
    """Test Knowledge Base Agent tool loading."""

    @pytest.mark.asyncio
    async def test_tool_loading_success(self) -> None:
        """Test successful tool loading from MCP server."""
        agent = KnowledgeBaseAgent()

        with (
            patch("core_agents.base_agent.MCPClient") as mock_client_class,
            patch("core_agents.base_agent.MCPToolConverter") as mock_converter_class,
            patch("core_agents.base_agent.Agent") as mock_agent_class,
        ):
            mock_client = AsyncMock()
            mock_converter = AsyncMock()

            async def mock_tool_func(**kwargs):  # type: ignore[no-untyped-def]
                return {}

            mock_tool_func.__name__ = "kb_search_corpus_tool"
            mock_converter.load_tools = AsyncMock(return_value=[mock_tool_func])

            mock_client_class.return_value = mock_client
            mock_converter_class.return_value = mock_converter
            mock_agent_class.return_value = MagicMock()

            await agent.initialize()

            mock_converter.load_tools.assert_called_once()
            mock_agent_class.assert_called_once()
            call_kwargs = mock_agent_class.call_args.kwargs
            assert "tools" in call_kwargs
            assert len(call_kwargs["tools"]) == 1


class TestKnowledgeBaseAgentContextManager:
    """Test Knowledge Base Agent context manager."""

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

            async with KnowledgeBaseAgent() as agent:
                assert agent._initialized
                assert agent.agent is not None

            mock_client.close.assert_called_once()


class TestKnowledgeBaseAgentConvenienceFunction:
    """Test convenience function for agent creation."""

    @pytest.mark.asyncio
    async def test_create_knowledge_base_agent(self) -> None:
        """Test create_knowledge_base_agent helper function."""
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

            agent = await create_knowledge_base_agent()

            assert agent is not None
            assert agent._initialized
            assert agent.agent is not None

            await agent.close()
