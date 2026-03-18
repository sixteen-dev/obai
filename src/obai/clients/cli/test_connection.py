#!/usr/bin/env python3
"""Quick test script to verify MCP server connections.

Run this before using chat.py to ensure all MCP servers are reachable.

Usage:
    python test_connection.py
"""

import asyncio
import sys
from pathlib import Path

# Add OBaI root to path so we can import agents package
obai_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(obai_root))

try:
    from core_agents.config import get_config
    from core_agents.mcp import MCPClient
except ImportError as e:
    print(f"Error importing: {e}")
    print("Make sure you're running from OBaI directory or core_agents package exists")
    print(f"OBaI root: {obai_root}")
    sys.exit(1)


class Colors:
    """ANSI color codes."""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


async def test_mcp_server(name: str, url: str) -> bool:
    """Test connection to a single MCP server.

    Args:
        name: Human-readable name for the server.
        url: MCP server URL.

    Returns:
        True if server is reachable and responding, False otherwise.
    """
    print(f"\n{Colors.CYAN}Testing {name}...{Colors.END}")
    print(f"  URL: {url}")

    try:
        client = MCPClient(base_url=url, timeout=10.0)
        tools = await client.list_tools()

        print(f"  {Colors.GREEN}✓ Connected{Colors.END}")
        print(f"  {Colors.GREEN}✓ {len(tools)} tools available{Colors.END}")

        # Show first 3 tools
        if tools:
            tool_names = [t["name"] for t in tools[:3]]
            print(f"  {Colors.CYAN}  Sample tools: {', '.join(tool_names)}{Colors.END}")

        await client.close()
        return True

    except Exception as e:
        print(f"  {Colors.RED}✗ Failed: {e}{Colors.END}")
        return False


async def main() -> None:
    """Run connection tests for all MCP servers."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}  MCP Server Connection Test{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.END}")

    # Load config
    try:
        config = get_config()
    except Exception as e:
        print(f"\n{Colors.RED}Failed to load config: {e}{Colors.END}")
        print(f"\n{Colors.YELLOW}Make sure environment variables are set:{Colors.END}")
        print("  - OPENAI_API_KEY")
        print("  - MCP_FUNDAMENTALS_URL")
        print("  - MCP_MARKET_DATA_URL")
        print("  - MCP_EVENTS_NEWS_URL")
        print("  - MCP_OPTIONS_URL")
        sys.exit(1)

    # Test each server
    servers = [
        ("Fundamentals Server", config.mcp_fundamentals_url),
        ("Market Data Server", config.mcp_market_data_url),
        ("Events/News Server", config.mcp_events_news_url),
        ("Options Server", config.mcp_options_url),
    ]

    results = []
    for name, url in servers:
        success = await test_mcp_server(name, url)
        results.append((name, success))

    # Summary
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}  Summary{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.END}\n")

    total = len(results)
    passed = sum(1 for _, success in results if success)
    failed = total - passed

    for name, success in results:
        status = (
            f"{Colors.GREEN}✓ PASS{Colors.END}" if success else f"{Colors.RED}✗ FAIL{Colors.END}"
        )
        print(f"  {status}  {name}")

    print(f"\n{Colors.BOLD}Total: {passed}/{total} servers reachable{Colors.END}")

    if failed > 0:
        print(f"\n{Colors.YELLOW}⚠ {failed} server(s) failed to connect{Colors.END}")
        print(
            f"{Colors.YELLOW}  Make sure the servers are running and URLs are correct{Colors.END}\n"
        )
        sys.exit(1)
    else:
        print(f"\n{Colors.GREEN}✓ All servers ready! You can now run chat.py{Colors.END}\n")
        sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n{Colors.CYAN}Interrupted{Colors.END}\n")
        sys.exit(1)
