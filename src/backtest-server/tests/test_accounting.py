"""Tests for capital allocation logic."""

from __future__ import annotations

from src.engine.accounting import allocate_capital


class TestAllocateEqualWeight:
    """Test equal_weight allocation method."""

    def test_three_signals_enough_cash_for_all(self) -> None:
        """Three signals with enough cash should produce 3 allocations."""
        signals = [
            ("AAPL", 150.0, 1),
            ("MSFT", 200.0, 2),
            ("GOOG", 100.0, 3),
        ]
        result = allocate_capital(
            cash=100_000.0,
            total_equity=100_000.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=40.0,
            max_positions=5,
            current_position_count=0,
        )
        assert len(result) == 3
        symbols = [r[0] for r in result]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        assert "GOOG" in symbols
        # All shares should be positive integers
        for _, shares, cost in result:
            assert shares > 0
            assert cost > 0

    def test_insufficient_cash_for_all(self) -> None:
        """Cash for only 1 allocation; earliest signal gets it."""
        signals = [
            ("AAPL", 150.0, 5),
            ("MSFT", 80.0, 3),
            ("GOOG", 100.0, 10),
        ]
        # max_positions=3, so equal_weight allocates $300/3 = $100 each.
        # Sorted by signal idx: MSFT(3), AAPL(5), GOOG(10).
        # MSFT at $80: 1 share ($80). AAPL at $150: 0 shares (skip).
        # GOOG at $100: 1 share ($100).
        result = allocate_capital(
            cash=300.0,
            total_equity=300.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=3,
            current_position_count=0,
        )
        assert len(result) >= 1
        # MSFT should be first (earliest signal at idx=3)
        assert result[0][0] == "MSFT"
        # AAPL should be skipped ($100 alloc can't buy 1 share at $150)
        allocated_symbols = {r[0] for r in result}
        assert "AAPL" not in allocated_symbols

    def test_max_positions_limit(self) -> None:
        """Max_positions=2 with 1 already held should allow only 1 new."""
        signals = [
            ("AAPL", 100.0, 1),
            ("MSFT", 100.0, 2),
            ("GOOG", 100.0, 3),
        ]
        result = allocate_capital(
            cash=100_000.0,
            total_equity=100_000.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=2,
            current_position_count=1,
        )
        assert len(result) == 1
        assert result[0][0] == "AAPL"  # Earliest signal (idx=1)

    def test_signal_recency_priority(self) -> None:
        """Signal at idx 5 should get capital before signal at idx 10."""
        signals = [
            ("LATE", 100.0, 10),
            ("EARLY", 100.0, 5),
        ]
        result = allocate_capital(
            cash=200.0,
            total_equity=200.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=2,
            current_position_count=0,
        )
        assert len(result) >= 1
        # EARLY (idx=5) should be first
        assert result[0][0] == "EARLY"

    def test_alphabetical_tiebreak(self) -> None:
        """Same idx should use alphabetical order."""
        signals = [
            ("ZZZZ", 10.0, 5),
            ("AAAA", 10.0, 5),
            ("MMMM", 10.0, 5),
        ]
        result = allocate_capital(
            cash=100_000.0,
            total_equity=100_000.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=3,
            current_position_count=0,
        )
        symbols = [r[0] for r in result]
        assert symbols == ["AAAA", "MMMM", "ZZZZ"]

    def test_discrete_shares(self) -> None:
        """$1000 at $30/share should yield 33 shares, leaving cash remainder."""
        signals = [("TEST", 30.0, 1)]
        result = allocate_capital(
            cash=1000.0,
            total_equity=1000.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,
            current_position_count=0,
        )
        assert len(result) == 1
        _, shares, cost = result[0]
        assert shares == 33
        assert cost == 33 * 30.0
        # Remainder should be $10 (stays as cash, not in allocation)

    def test_zero_cash_returns_empty(self) -> None:
        """Zero cash should return empty list."""
        signals = [("AAPL", 100.0, 1)]
        result = allocate_capital(
            cash=0.0,
            total_equity=100_000.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=20.0,
            max_positions=5,
            current_position_count=0,
        )
        assert result == []

    def test_equal_weight_respects_max_position_pct(self) -> None:
        """Equal weight must cap each allocation at max_position_pct of equity.

        With $100k cash, $100k equity, max_positions=5, max_position_pct=10,
        and 3 signals at $50/share: each allocation should be capped at $10k
        (200 shares), not $33k from equal_weight splitting.
        """
        signals = [
            ("AAPL", 50.0, 1),
            ("MSFT", 50.0, 2),
            ("GOOG", 50.0, 3),
        ]
        result = allocate_capital(
            cash=100_000.0,
            total_equity=100_000.0,
            signals=signals,
            method="equal_weight",
            max_position_pct=10.0,
            max_positions=5,
            current_position_count=0,
        )
        assert len(result) == 3
        for symbol, shares, cost in result:
            # max_position_pct=10% of $100k = $10k cap
            assert shares == 200, f"{symbol}: expected 200 shares, got {shares}"
            assert cost == 10_000.0, f"{symbol}: expected $10k cost, got {cost}"


class TestAllocateFixedPct:
    """Test fixed_pct allocation method."""

    def test_allocates_correct_percentage(self) -> None:
        """Fixed_pct should allocate max_position_pct of equity."""
        signals = [("AAPL", 100.0, 1)]
        result = allocate_capital(
            cash=100_000.0,
            total_equity=100_000.0,
            signals=signals,
            method="fixed_pct",
            max_position_pct=10.0,
            max_positions=5,
            current_position_count=0,
        )
        assert len(result) == 1
        _, shares, cost = result[0]
        # 10% of $100k = $10k, at $100/share = 100 shares
        assert shares == 100
        assert cost == 10_000.0


class TestAtrRiskShares:
    """Test atr_risk allocation: shares come from a budgeted loss, then caps."""

    def _allocate(
        self,
        cash: float,
        max_position_pct: float,
        commission_pct: float = 0.0,
    ) -> int:
        """Allocate one $50 signal against a $1000 budget over a $5 stop distance."""
        result = allocate_capital(
            cash=cash,
            total_equity=100_000.0,
            signals=[("AAPL", 50.0, 1)],
            method="atr_risk",
            max_position_pct=max_position_pct,
            max_positions=5,
            current_position_count=0,
            commission_pct=commission_pct,
            risk_budget=1_000.0,
            stop_distances={"AAPL": 5.0},
        )
        return result[0][1] if result else 0

    def test_budget_over_distance_floors_and_caps(self) -> None:
        """The budget sets the share count; exposure and cash only reduce it."""
        # 1000 / 5 = 200 shares; the 20% exposure cap ($20k) is not binding.
        assert self._allocate(cash=100_000.0, max_position_pct=20.0) == 200
        # 5% of equity is $5k, which buys 100 shares at $50.
        assert self._allocate(cash=100_000.0, max_position_pct=5.0) == 100
        # $2020 at $50 plus 1% commission ($50.50 each) buys 40.
        assert self._allocate(cash=2_020.0, max_position_pct=20.0, commission_pct=1.0) == 40
