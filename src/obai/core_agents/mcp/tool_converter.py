"""Convert MCP tools to OpenAI Agent SDK compatible tools."""

import json
import logging
import re
from typing import Any

from agents import FunctionTool
from agents.run_context import RunContextWrapper
from cachetools import TTLCache

from .client import MCPClient

logger = logging.getLogger(__name__)


class ToolCallCache:
    """In-memory cache for tool call results with TTL and LRU eviction.

    Wraps cachetools.TTLCache with stats tracking and tool-specific API.
    Uses (tool_name, normalized_args_json) as cache key.

    Example:
        ```python
        cache = ToolCallCache(ttl_seconds=60)

        # Check cache before calling
        cached = cache.get("my_tool", '{"symbol": "AAPL"}')
        if cached:
            return cached

        # Call MCP and cache result
        result = await mcp_client.call_tool(...)
        cache.set("my_tool", '{"symbol": "AAPL"}', result_json)
        ```
    """

    def __init__(self, maxsize: int = 500, ttl_seconds: int = 60) -> None:
        """Initialize cache.

        Args:
            maxsize: Maximum number of entries (LRU eviction when exceeded).
            ttl_seconds: Time-to-live for cached entries (default: 60s).
        """
        self._cache: TTLCache[str, str] = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._hits = 0
        self._misses = 0

    def _make_key(self, tool_name: str, args_json: str) -> str:
        """Create cache key from tool name and normalized args."""
        return f"{tool_name}:{args_json}"

    def get(self, tool_name: str, args_json: str) -> str | None:
        """Get cached result if exists and not expired.

        Args:
            tool_name: Name of the MCP tool.
            args_json: JSON-encoded normalized arguments.

        Returns:
            Cached result JSON string, or None if not found/expired.
        """
        key = self._make_key(tool_name, args_json)
        result = self._cache.get(key)

        if result is None:
            self._misses += 1
            return None

        self._hits += 1
        return result

    def set(self, tool_name: str, args_json: str, result_json: str) -> None:
        """Store result in cache.

        Args:
            tool_name: Name of the MCP tool.
            args_json: JSON-encoded normalized arguments.
            result_json: JSON-encoded result to cache.
        """
        key = self._make_key(tool_name, args_json)
        self._cache[key] = result_json

    def clear(self) -> None:
        """Clear all cached entries and reset stats."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
        }


def normalize_args(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool arguments for consistent cache keys.

    Applies normalization rules:
    - Remove null/empty values
    - Fill default values from schema
    - Sort list values when order is not meaningful
    - Normalize date strings to YYYY-MM-DD
    - Lowercase enum-like strings

    Args:
        args: Raw arguments dict.
        schema: Tool input schema with properties and defaults.

    Returns:
        Normalized arguments dict.
    """
    properties = schema.get("properties", {})
    normalized: dict[str, Any] = {}

    for key, value in args.items():
        # Skip null/empty values
        if value is None or value == "" or value == []:
            continue

        prop_schema = properties.get(key, {})

        # Normalize based on type
        if isinstance(value, str):
            # Check if it's a date-like string (YYYY-MM-DD or similar)
            if _looks_like_date(value):
                value = _normalize_date(value)
            # Check if it's an enum (lowercase for case-insensitive matching)
            elif "enum" in prop_schema:
                value = value.lower()
            # Normalize ticker symbols (uppercase)
            elif key in ("symbol", "ticker", "tickers"):
                value = value.upper()

        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            # Sort string lists (tickers, symbols, etc.)
            if key in ("symbols", "tickers"):
                value = sorted(v.upper() for v in value)
            else:
                value = sorted(value)

        normalized[key] = value

    # Apply defaults from schema for missing required fields
    for key, prop_schema in properties.items():
        if key not in normalized and "default" in prop_schema:
            normalized[key] = prop_schema["default"]

    return normalized


def _looks_like_date(value: str) -> bool:
    """Check if string looks like a date."""
    date_patterns = [
        r"^\d{4}-\d{2}-\d{2}$",  # YYYY-MM-DD
        r"^\d{2}/\d{2}/\d{4}$",  # MM/DD/YYYY
        r"^\d{4}/\d{2}/\d{2}$",  # YYYY/MM/DD
    ]
    return any(re.match(pattern, value) for pattern in date_patterns)


def _normalize_date(value: str) -> str:
    """Normalize date string to YYYY-MM-DD format."""
    # Already in correct format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value

    # MM/DD/YYYY -> YYYY-MM-DD
    match = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", value)
    if match:
        month, day, year = match.groups()
        return f"{year}-{month}-{day}"

    # YYYY/MM/DD -> YYYY-MM-DD
    match = re.match(r"^(\d{4})/(\d{2})/(\d{2})$", value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"

    # Return as-is if unrecognized
    return value


# Global cache instance (lazy initialized with config values)
_tool_cache: ToolCallCache | None = None


def get_tool_cache() -> ToolCallCache:
    """Get the global tool call cache instance.

    Lazy initializes with config values on first access.

    Returns:
        The shared ToolCallCache instance.
    """
    global _tool_cache
    if _tool_cache is None:
        # Import here to avoid circular imports
        from core_agents.config import get_config

        config = get_config()
        _tool_cache = ToolCallCache(
            maxsize=config.tool_cache_maxsize,
            ttl_seconds=config.tool_cache_ttl,
        )
        logger.info(
            f"Tool cache initialized (maxsize={config.tool_cache_maxsize}, "
            f"ttl={config.tool_cache_ttl}s)"
        )
    return _tool_cache


def clear_tool_cache() -> None:
    """Clear the global tool call cache.

    Call this between runs to ensure fresh data.
    """
    cache = get_tool_cache()
    cache.clear()
    logger.debug("Tool call cache cleared")


class MCPToolConverter:
    """Converts MCP server tools to Agent SDK compatible FunctionTool instances.

    The Agent SDK expects tools as FunctionTool objects with JSON schemas.
    This converter dynamically creates these tools that call MCP servers via HTTP.

    Example:
        ```python
        converter = MCPToolConverter("http://localhost:8001/mcp")
        tools = await converter.load_tools()

        # Use with Agent SDK
        from agents import Agent
        agent = Agent(name="market_data", tools=tools)
        ```
    """

    def __init__(self, mcp_client: MCPClient) -> None:
        """Initialize tool converter.

        Args:
            mcp_client: MCP client instance for communicating with server.
        """
        self.client = mcp_client
        self._tools_cache: dict[str, FunctionTool] | None = None

    async def load_tools(self) -> list[FunctionTool]:
        """Load and convert all tools from MCP server.

        Fetches tool definitions from MCP server and converts each to an
        Agent SDK compatible FunctionTool.

        Returns:
            List of FunctionTool instances.

        Raises:
            MCPClientError: If tool loading fails.
        """
        tool_definitions = await self.client.list_tools()
        logger.info(f"Loading {len(tool_definitions)} tools from {self.client.base_url}")

        tools: list[FunctionTool] = []

        for tool_def in tool_definitions:
            tool_name = tool_def.get("name")
            if not tool_name:
                logger.warning("Skipping tool with missing name")
                continue

            tool = self._create_function_tool(tool_def)
            tools.append(tool)
            logger.debug(f"Loaded tool: {tool_name}")

        self._tools_cache = {tool.name: tool for tool in tools}
        logger.info(f"Successfully loaded {len(tools)} tools")
        return tools

    def _create_function_tool(self, tool_def: dict[str, Any]) -> FunctionTool:
        """Create a FunctionTool for a single MCP tool.

        Args:
            tool_def: Tool definition from MCP server with name, description, schema.

        Returns:
            FunctionTool that invokes the MCP tool.
        """
        tool_name: str = tool_def["name"]
        tool_description: str = tool_def.get("description", "")
        input_schema: dict[str, Any] = tool_def.get("inputSchema", {})

        # A tool is cacheable only when its server promises BOTH read-only
        # *and* idempotent behavior. `readOnlyHint` alone is not enough:
        # current quotes, movers, and live odds are read-only but their
        # output changes between calls, so their authors set
        # `idempotentHint=False` to opt out of caching. Treating either hint
        # as sufficient (OR) would serve stale "current data" for up to the
        # cache TTL.
        annotations = tool_def.get("annotations", {})
        is_cacheable = annotations.get("readOnlyHint", False) and annotations.get(
            "idempotentHint", False
        )

        # Keep references for the invoke function closure
        mcp_client = self.client
        schema = input_schema  # For normalization

        async def invoke_tool(
            ctx: RunContextWrapper[Any],  # noqa: ARG001
            args: str,
        ) -> str:
            """Invoke the MCP tool with provided arguments.

            Args:
                ctx: Run context from Agent SDK.
                args: JSON-encoded arguments string.

            Returns:
                JSON-encoded result string.
            """
            try:
                # Parse JSON arguments
                parsed_args: dict[str, Any] = json.loads(args) if args else {}

                # Normalize arguments for cache key
                normalized = normalize_args(parsed_args, schema)
                cache_key = json.dumps(normalized, sort_keys=True)

                # Check cache for cacheable tools
                cache = get_tool_cache()
                if is_cacheable:
                    cached_result = cache.get(tool_name, cache_key)
                    if cached_result is not None:
                        logger.debug(f"Cache HIT: {tool_name}")
                        return cached_result

                logger.debug(f"Executing MCP tool: {tool_name} with args: {args}")

                # Call MCP server
                result = await mcp_client.call_tool(tool_name, parsed_args)

                # Return as JSON string
                result_json = json.dumps(result)

                # Cache result for cacheable tools (only if not an error)
                if is_cacheable and not (isinstance(result, dict) and result.get("isError")):
                    cache.set(tool_name, cache_key, result_json)

                return result_json

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON args for {tool_name}: {e}")
                return json.dumps({"isError": True, "error": f"Invalid JSON: {e}"})

            except Exception as e:
                logger.error(f"Error executing {tool_name}: {e}")
                return json.dumps(
                    {
                        "isError": True,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                )

        # Create FunctionTool with JSON schema
        return FunctionTool(
            name=tool_name,
            description=tool_description,
            params_json_schema=input_schema,
            on_invoke_tool=invoke_tool,
        )

    def get_tool_by_name(self, name: str) -> FunctionTool | None:
        """Get a specific tool by name.

        Args:
            name: Tool name.

        Returns:
            FunctionTool if found, None otherwise.
        """
        if self._tools_cache is None:
            logger.warning("Tools not loaded yet. Call load_tools() first.")
            return None

        return self._tools_cache.get(name)

    async def close(self) -> None:
        """Close the underlying MCP client."""
        await self.client.close()
