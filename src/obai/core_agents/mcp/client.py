"""MCP client wrapper using FastMCP's native client.

Uses fastmcp.Client for proper MCP protocol support over Streamable HTTP.
"""

import json
import logging
from typing import Any

from fastmcp import Client as FastMCPClient
from mcp.types import Tool

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Base exception for MCP client errors."""


class MCPConnectionError(MCPClientError):
    """Failed to connect to MCP server."""


class MCPTimeoutError(MCPClientError):
    """Request to MCP server timed out."""


class MCPServerError(MCPClientError):
    """MCP server returned an error response."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize MCP server error.

        Args:
            message: Error message.
            status_code: HTTP status code (if applicable).
            response_data: Full response data from server (if JSON).
        """
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class MCPClient:
    """Async client for MCP servers using FastMCP's native client.

    Handles communication with MCP servers via Streamable HTTP transport,
    which is the proper MCP protocol for HTTP-based servers.

    Example:
        ```python
        async with MCPClient("http://localhost:8001/mcp") as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_quote", {"symbol": "AAPL"})
        ```
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        """Initialize MCP client.

        Args:
            base_url: Base URL of MCP server (e.g., http://localhost:8001/mcp).
            timeout: Request timeout in seconds (passed to underlying client).
            max_retries: Maximum number of retry attempts (for future use).
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: FastMCPClient[Any] | None = None
        self._context_manager: FastMCPClient[Any] | None = None
        logger.info(f"Initialized MCP client for {self.base_url}")

    async def _ensure_connected(self) -> FastMCPClient[Any]:
        """Ensure client is connected, connecting if necessary.

        Returns:
            Connected FastMCP client.

        Raises:
            MCPConnectionError: If connection fails.
        """
        if self._client is not None:
            return self._client

        try:
            # FastMCP Client uses async context manager pattern
            self._context_manager = FastMCPClient(self.base_url)
            self._client = await self._context_manager.__aenter__()  # type: ignore[no-untyped-call]
            logger.debug(f"Connected to MCP server at {self.base_url}")
            return self._client
        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            raise MCPConnectionError(f"Failed to connect to {self.base_url}: {e}") from e

    async def list_tools(self) -> list[dict[str, Any]]:
        """List all available tools from MCP server.

        Returns:
            List of tool definitions with name, description, and input schema.

        Raises:
            MCPServerError: If server returns error response.
            MCPConnectionError: If connection to server fails.
            MCPTimeoutError: If request times out.
            MCPClientError: For other request failures.
        """
        logger.debug(f"Listing tools from {self.base_url}")

        try:
            client = await self._ensure_connected()
            tools: list[Tool] = await client.list_tools()

            # Convert to dict format for compatibility with tool converter
            tool_dicts: list[dict[str, Any]] = []
            for tool in tools:
                tool_dict: dict[str, Any] = {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
                }
                tool_dicts.append(tool_dict)

            logger.info(f"Found {len(tool_dicts)} tools from {self.base_url}")
            return tool_dicts

        except MCPConnectionError:
            raise
        except TimeoutError as e:
            logger.error(f"Timeout listing tools from {self.base_url}")
            raise MCPTimeoutError(f"Timeout connecting to {self.base_url}") from e
        except Exception as e:
            logger.error(f"Error listing tools: {e}")
            raise MCPClientError(f"Failed to list tools: {e}") from e

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call an MCP tool with given arguments.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Tool arguments as key-value pairs.

        Returns:
            Tool execution result.

        Raises:
            MCPServerError: If server returns error response.
            MCPConnectionError: If connection to server fails.
            MCPTimeoutError: If request times out.
            MCPClientError: For other request failures.
        """
        logger.debug(f"Calling tool {tool_name} with args: {arguments}")

        try:
            client = await self._ensure_connected()
            result = await client.call_tool(tool_name, arguments)

            # Already a plain dict — return directly
            if isinstance(result, dict):
                return result

            # MCP SDK returns CallToolResult (Pydantic model) with a
            # .content list of TextContent/ImageContent items. Access
            # .content directly — do NOT iterate the model itself
            # (Pydantic __iter__ yields (field_name, value) tuples).
            content_items: list[Any] = []
            if hasattr(result, "content") and isinstance(result.content, list):
                content_items = result.content
            elif isinstance(result, list):
                content_items = result

            if content_items:
                contents: list[str] = []
                for item in content_items:
                    if hasattr(item, "text"):
                        contents.append(item.text)
                    elif hasattr(item, "data"):
                        contents.append(str(item.data))
                    else:
                        contents.append(str(item))

                if len(contents) == 1:
                    try:
                        parsed = json.loads(contents[0])
                        if isinstance(parsed, dict):
                            return parsed
                        return {"content": parsed}
                    except (json.JSONDecodeError, TypeError):
                        return {"content": contents[0]}
                return {"contents": contents}

            # Fallback for other types
            return {"result": str(result)}

        except MCPConnectionError:
            raise
        except TimeoutError as e:
            logger.error(f"Timeout calling {tool_name}")
            raise MCPTimeoutError(f"Timeout calling {tool_name}") from e
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error calling {tool_name}: {error_msg}")

            # Check if it's a server-side error
            if "error" in error_msg.lower():
                raise MCPServerError(f"Tool {tool_name} failed: {error_msg}") from e

            raise MCPClientError(f"Failed to call {tool_name}: {e}") from e

    async def close(self) -> None:
        """Close the client connection and release resources."""
        if self._context_manager is not None:
            try:
                await self._context_manager.__aexit__(None, None, None)  # type: ignore[no-untyped-call]
            except Exception as e:
                logger.warning(f"Error closing MCP client: {e}")
            finally:
                self._client = None
                self._context_manager = None
        logger.debug(f"Closed MCP client for {self.base_url}")

    async def __aenter__(self) -> "MCPClient":
        """Context manager entry."""
        await self._ensure_connected()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Context manager exit."""
        await self.close()
