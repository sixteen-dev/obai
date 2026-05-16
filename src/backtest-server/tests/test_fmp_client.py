"""Tests for FMP client helpers."""

from __future__ import annotations

from src.clients.fmp_client import _to_fmp_symbol


class TestToFmpSymbol:
    """Ticker normalization for the FMP HTTP layer."""

    def test_plain_ticker_unchanged(self) -> None:
        assert _to_fmp_symbol("AAPL") == "AAPL"

    def test_share_class_dot_becomes_dash(self) -> None:
        # FMP returns HTTP 402 for dotted share-class tickers
        # (BRK.B, BF.B). The dash form is the canonical request shape.
        assert _to_fmp_symbol("BRK.B") == "BRK-B"
        assert _to_fmp_symbol("BRK.A") == "BRK-A"
        assert _to_fmp_symbol("BF.B") == "BF-B"

    def test_multiple_dots_all_converted(self) -> None:
        # Defensive: any future ticker with multiple separators normalizes consistently.
        assert _to_fmp_symbol("A.B.C") == "A-B-C"

    def test_already_dashed_unchanged(self) -> None:
        # If a caller already passed canonical form, normalization is a no-op.
        assert _to_fmp_symbol("BRK-B") == "BRK-B"

    def test_empty_string(self) -> None:
        assert _to_fmp_symbol("") == ""
