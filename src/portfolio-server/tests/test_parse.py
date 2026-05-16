"""Tests for portfolio position parsing."""

from src.tools.parse import parse_positions


class TestParsePositions:
    """Tests for parse_positions function."""

    def test_parse_percentage_format(self, sample_portfolio_text: str) -> None:
        """Test parsing percentage format."""
        result = parse_positions(sample_portfolio_text)

        assert "isError" not in result
        assert result["position_count"] == 3

        portfolio = result["portfolio"]
        positions = portfolio["positions"]

        # Check AAPL
        aapl = next(p for p in positions if p["symbol"] == "AAPL")
        assert abs(aapl["weight"] - 0.40) < 0.01
        assert aapl["asset_type"] == "stock"

        # Check QQQ (ETF)
        qqq = next(p for p in positions if p["symbol"] == "QQQ")
        assert abs(qqq["weight"] - 0.35) < 0.01
        assert qqq["asset_type"] == "etf"

        # Check BND (Bond ETF)
        bnd = next(p for p in positions if p["symbol"] == "BND")
        assert abs(bnd["weight"] - 0.25) < 0.01
        assert bnd["asset_type"] == "bond_etf"

    def test_parse_decimal_format(self, sample_portfolio_decimal: str) -> None:
        """Test parsing decimal format."""
        result = parse_positions(sample_portfolio_decimal)

        assert "isError" not in result
        assert result["position_count"] == 3

        portfolio = result["portfolio"]
        total_weight = portfolio["total_weight"]
        assert abs(total_weight - 1.0) < 0.01

    def test_parse_dollar_format(self, sample_portfolio_dollars: str) -> None:
        """Test parsing dollar format."""
        result = parse_positions(sample_portfolio_dollars)

        assert "isError" not in result
        assert result["position_count"] == 3

        portfolio = result["portfolio"]
        positions = portfolio["positions"]

        # Check weights are proportional to dollar amounts
        aapl = next(p for p in positions if p["symbol"] == "AAPL")
        assert abs(aapl["weight"] - 0.50) < 0.01  # $50k / $100k total

    def test_parse_dollar_k_suffix(self) -> None:
        """`$50k AAPL` must parse as $50,000, not $50."""
        result = parse_positions("$50k AAPL, $50k MSFT")

        assert "isError" not in result
        assert result["position_count"] == 2

        portfolio = result["portfolio"]
        aapl = next(p for p in portfolio["positions"] if p["symbol"] == "AAPL")
        assert abs(aapl["weight"] - 0.50) < 0.01

    def test_parse_dollar_million_suffix(self) -> None:
        """`$1.2M` is treated as $1,200,000 and works for share-class tickers."""
        result = parse_positions("$1.2M BRK.B, $300k AAPL")

        assert "isError" not in result
        assert result["position_count"] == 2

        portfolio = result["portfolio"]
        brk = next(p for p in portfolio["positions"] if p["symbol"] == "BRK.B")
        # $1.2M / ($1.2M + $0.3M) = 0.8
        assert abs(brk["weight"] - 0.80) < 0.01

    def test_parse_mixed_asset_types(self, sample_portfolio_mixed: str) -> None:
        """Test detection of different asset types."""
        result = parse_positions(sample_portfolio_mixed)

        assert "isError" not in result

        portfolio = result["portfolio"]
        positions = portfolio["positions"]

        # SPY should be ETF
        spy = next(p for p in positions if p["symbol"] == "SPY")
        assert spy["asset_type"] == "etf"

        # BND should be Bond ETF
        bnd = next(p for p in positions if p["symbol"] == "BND")
        assert bnd["asset_type"] == "bond_etf"

        # CASH should be cash
        cash = next(p for p in positions if p["symbol"] == "CASH")
        assert cash["asset_type"] == "cash"

    def test_parse_empty_input(self) -> None:
        """Test error handling for empty input."""
        result = parse_positions("")

        assert result["isError"] is True
        assert "error_type" in result

    def test_parse_invalid_input(self) -> None:
        """Test error handling for unparseable input."""
        result = parse_positions("this is not a portfolio")

        assert result["isError"] is True
        assert "error_type" in result

    def test_parse_normalizes_weights(self) -> None:
        """Test that weights are normalized to sum to 1."""
        result = parse_positions("AAPL 60%, MSFT 60%")  # Sums to 120%

        assert "isError" not in result

        portfolio = result["portfolio"]
        total_weight = portfolio["total_weight"]
        assert abs(total_weight - 1.0) < 0.01
        assert portfolio["normalized"] is True

    def test_parse_warns_on_over_100(self) -> None:
        """Test warning for weights exceeding 100%."""
        result = parse_positions("AAPL 60%, MSFT 60%")

        assert "isError" not in result
        assert any(">100%" in w or "normalize" in w.lower() for w in result["warnings"])

    def test_parse_case_insensitive(self) -> None:
        """Test that symbol parsing is case-insensitive."""
        result = parse_positions("aapl 50%, msft 50%")

        assert "isError" not in result

        portfolio = result["portfolio"]
        positions = portfolio["positions"]
        symbols = [p["symbol"] for p in positions]

        assert "AAPL" in symbols
        assert "MSFT" in symbols

    def test_parse_various_separators(self) -> None:
        """Test parsing with different separators."""
        # Semicolon
        result1 = parse_positions("AAPL 50%; MSFT 50%")
        assert result1["position_count"] == 2

        # Newline (via string)
        result2 = parse_positions("AAPL 50%\nMSFT 50%")
        assert result2["position_count"] == 2

        # Colon format
        result3 = parse_positions("AAPL: 50%, MSFT: 50%")
        assert result3["position_count"] == 2

    def test_parse_handles_duplicates(self) -> None:
        """Test that duplicate symbols are handled."""
        result = parse_positions("AAPL 40%, AAPL 30%, MSFT 30%")

        assert "isError" not in result
        # Should use first occurrence
        assert result["position_count"] == 2
        assert any("duplicate" in w.lower() for w in result["warnings"])
