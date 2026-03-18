"""Entry point for agents package.

This module provides a simple CLI for testing agents locally.
For production use (Discord bot, web API), use specific client implementations.
"""

import asyncio
import logging
import sys

from .config import get_config
from .market_data_agent import create_market_data_agent


async def main() -> None:
    """Run a simple test query with Market Data Agent."""
    config = get_config()

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger(__name__)
    logger.info("OBaI Agents - Test Mode")
    logger.info(f"OpenAI API key configured: {bool(config.openai_api_key)}")
    logger.info(f"MCP servers: {config.mcp_market_data_url}")

    # Test with Market Data Agent
    logger.info("Initializing Market Data Agent...")
    agent = await create_market_data_agent()
    logger.info("✓ Market Data Agent initialized successfully")

    # Close agent
    await agent.close()
    logger.info("Agent closed. Test complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
