"""MCP server for events, news, and time-sensitive market catalysts."""

from pathlib import Path

__version__ = (Path(__file__).parent.parent / "VERSION").read_text().strip()

__all__ = ["__version__"]
