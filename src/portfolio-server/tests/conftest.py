"""Pytest fixtures for portfolio-server tests."""

import pytest


@pytest.fixture
def sample_portfolio_text() -> str:
    """Sample portfolio text in percentage format."""
    return "AAPL 40%, QQQ 35%, BND 25%"


@pytest.fixture
def sample_portfolio_decimal() -> str:
    """Sample portfolio text in decimal format."""
    return "AAPL 0.40, QQQ 0.35, BND 0.25"


@pytest.fixture
def sample_portfolio_mixed() -> str:
    """Sample portfolio text with mixed asset types."""
    return "SPY 50%, BND 30%, CASH 20%"


@pytest.fixture
def sample_portfolio_dollars() -> str:
    """Sample portfolio text in dollar format."""
    return "$50,000 AAPL, $30,000 QQQ, $20,000 BND"
