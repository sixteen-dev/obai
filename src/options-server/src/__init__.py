"""MCP server for options data and derivatives analytics."""

from pathlib import Path

__version__ = (Path(__file__).parent.parent / "VERSION").read_text().strip()

__all__ = ["__version__"]
