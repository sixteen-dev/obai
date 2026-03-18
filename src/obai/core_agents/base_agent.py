"""Base agent class for MCP-integrated specialist agents.

Provides common functionality for all specialist agents:
    - MCP client connection and tool loading
    - Resource lifecycle management with proper cleanup
    - Async context manager support
    - Standardized initialization pattern
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from agents import Agent, ModelSettings

from .config import AgentConfig, get_config
from .mcp import MCPClient, MCPToolConverter
from .prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for MCP-integrated specialist agents.

    Handles common patterns:
        - MCP client connection
        - Tool loading and conversion
        - Agent creation with OpenAI SDK
        - Resource cleanup on initialization failure
        - Async context manager pattern

    Subclasses must implement:
        - agent_type: Identifier for config/prompt lookup
        - mcp_url_property: Which config property has the MCP server URL

    Example:
        ```python
        class MarketDataAgent(BaseAgent):
            @property
            def agent_type(self) -> str:
                return "market_data"

            @property
            def mcp_url_property(self) -> str:
                return "mcp_market_data_url"
        ```
    """

    def __init__(self) -> None:
        """Initialize agent with config. Does not connect to MCP server."""
        self.config: AgentConfig = get_config()
        self.mcp_client: MCPClient | None = None
        self.tool_converter: MCPToolConverter | None = None
        self.agent: Agent[None] | None = None
        self._initialized = False
        logger.info(f"{self.agent_name} created (not initialized)")

    @property
    @abstractmethod
    def agent_type(self) -> str:
        """Agent type identifier for config and prompt lookup.

        Examples: "market_data", "fundamentals", "events_news", "options"
        """

    @property
    @abstractmethod
    def mcp_url_property(self) -> str:
        """Config property name for MCP server URL.

        Examples: "mcp_market_data_url", "mcp_fundamentals_url"
        """

    @property
    @abstractmethod
    def handoff_description(self) -> str:
        """Description shown to orchestrator for handoff decisions.

        This tells the orchestrator when to delegate to this agent.
        Example: "Specialist for real-time stock prices and technical analysis"
        """

    @property
    def agent_name(self) -> str:
        """Human-readable agent name for logging."""
        return f"OBaI {self.agent_type.replace('_', ' ').title()} Agent"

    @property
    def sdk_agent_name(self) -> str:
        """Agent name for OpenAI SDK (snake_case with prefix)."""
        return f"obai_{self.agent_type}_agent"

    def _get_mcp_url(self) -> str:
        """Get MCP server URL from config."""
        return str(getattr(self.config, self.mcp_url_property))

    def _get_model(self) -> str:
        """Get model for this agent. Override in subclasses for custom fallback."""
        return self.config.get_agent_model(self.agent_type)

    async def initialize(self) -> None:
        """Initialize agent by connecting to MCP server and loading tools.

        This async method must be called before using the agent.
        Properly cleans up resources if initialization fails partway through.

        Raises:
            MCPClientError: If connection to MCP server fails.
            MCPServerError: If MCP server returns error.
        """
        if self._initialized:
            logger.warning(f"{self.agent_name} already initialized")
            return

        mcp_url = self._get_mcp_url()
        logger.info(f"Initializing {self.agent_name} with {mcp_url}")

        try:
            # Create MCP client
            self.mcp_client = MCPClient(
                base_url=mcp_url,
                timeout=self.config.mcp_timeout,
                max_retries=self.config.mcp_max_retries,
            )

            # Create tool converter and load tools
            self.tool_converter = MCPToolConverter(self.mcp_client)
            tools = await self.tool_converter.load_tools()

            logger.info(f"Loaded {len(tools)} tools for {self.agent_name}")

            # Get configured model
            model = self._get_model()
            logger.info(f"{self.agent_name} using model: {model}")

            # Load instructions from prompt file
            instructions = load_prompt(self.agent_type)

            # Create agent with tools (Agent SDK uses OPENAI_API_KEY env var)
            self.agent = Agent(
                name=self.sdk_agent_name,
                handoff_description=self.handoff_description,
                instructions=instructions,
                model=model,
                tools=tools,  # type: ignore[arg-type]  # list covariance issue
                model_settings=ModelSettings(
                    parallel_tool_calls=True,  # Enable parallel tool execution
                ),
            )

            self._initialized = True
            logger.info(f"{self.agent_name} initialized successfully")

        except Exception:
            # Clean up on failure to prevent resource leaks
            logger.exception(f"{self.agent_name} initialization failed, cleaning up")
            await self._cleanup_resources()
            raise

    async def _cleanup_resources(self) -> None:
        """Clean up MCP client resources."""
        if self.mcp_client:
            try:
                await self.mcp_client.close()
            except Exception:
                logger.exception(f"Error closing MCP client for {self.agent_name}")
            finally:
                self.mcp_client = None

        self.tool_converter = None
        self.agent = None
        self._initialized = False

    async def close(self) -> None:
        """Close MCP client and release resources."""
        await self._cleanup_resources()
        logger.info(f"{self.agent_name} closed")

    async def __aenter__(self) -> "BaseAgent":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    def __repr__(self) -> str:
        """String representation for debugging."""
        status = "initialized" if self._initialized else "not initialized"
        return f"<{self.__class__.__name__} ({status})>"
