"""Tests for response filtering of reporting currency."""

from typing import Any

from src.response_filters import (
    filter_company_profile,
    filter_financial_ratios,
    filter_financial_statement,
    filter_key_metrics,
)


class TestReportingCurrencyPreserved:
    """Foreign filers report in home currency; currency must survive filtering."""

    def test_statement_preserves_reported_currency(self) -> None:
        """Test reportedCurrency survives the financial statement filter."""
        data: list[dict[str, Any]] = [
            {
                "date": "2024-03-31",
                "symbol": "TM",
                "reportedCurrency": "JPY",
                "cik": "0000320193",
                "revenue": 45095325000000,
                "netIncome": 4944933000000,
            }
        ]

        result = filter_financial_statement(data)

        assert result[0]["reportedCurrency"] == "JPY"
        assert "cik" not in result[0]

    def test_key_metrics_preserves_reported_currency(self) -> None:
        """Test reportedCurrency survives the key metrics filter."""
        data: list[dict[str, Any]] = [
            {
                "symbol": "TM",
                "date": "2024-03-31",
                "reportedCurrency": "JPY",
                "cik": "0000320193",
                "peRatio": 9.1,
            }
        ]

        result = filter_key_metrics(data)

        assert result[0]["reportedCurrency"] == "JPY"
        assert "cik" not in result[0]

    def test_ratios_preserves_reported_currency(self) -> None:
        """Test reportedCurrency survives the financial ratios filter."""
        data: list[dict[str, Any]] = [
            {
                "symbol": "TM",
                "date": "2024-03-31",
                "reportedCurrency": "JPY",
                "cik": "0000320193",
                "currentRatio": 1.2,
            }
        ]

        result = filter_financial_ratios(data)

        assert result[0]["reportedCurrency"] == "JPY"
        assert "cik" not in result[0]

    def test_profile_preserves_currency(self) -> None:
        """Test currency survives the company profile keep-list filter."""
        data: list[dict[str, Any]] = [
            {
                "symbol": "TM",
                "companyName": "Toyota Motor Corporation",
                "currency": "JPY",
                "sector": "Consumer Cyclical",
                "description": "dropped by keep-list",
            }
        ]

        result = filter_company_profile(data)

        assert result[0]["currency"] == "JPY"
        assert "description" not in result[0]
