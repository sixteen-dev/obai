"""Small Coinbase spot bar backtester for v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..models import Candle
from .metrics import compute_metrics

MIN_BACKTEST_CANDLES = 5
MIN_MEAN_REVERSION_WINDOW = 2
DEFAULT_INITIAL_CAPITAL_USD = 100_000.0
DEFAULT_TAKER_FEE_BPS = 60.0
DEFAULT_SPREAD_BPS = 5.0
DEFAULT_PARTICIPATION_SLIPPAGE_BPS_PER_PCT = 0.0
DEFAULT_MAX_POSITION_PCT = 1.0
DEFAULT_MAX_BAR_PARTICIPATION_PCT = 5.0
POSITION_EPSILON = 1e-12

# Every key each config accepts, including the aliases resolved below. An
# unrecognized key used to fall through to a default, so a caller that
# misspelled one ran a backtest it never asked for and was told it
# succeeded. Callers get the accepted set back instead.
STRATEGY_SPEC_KEYS = frozenset({"template", "parameters"})
# The same promise one level down. STRATEGY_SPEC_KEYS admits "parameters"
# wholesale, so a typo inside it kept falling through to a default -- the very
# thing the check above exists to stop. Which keys are valid depends on the
# template, and the check has to run before the defaults are read, because
# that is where the typo disappears.
TEMPLATE_PARAMETER_KEYS: dict[str, frozenset[str]] = {
    "spot_mean_reversion": frozenset({"window", "z_entry", "z_exit"}),
    "spot_trend_follow": frozenset({"fast_window", "slow_window"}),
}
EXECUTION_CONFIG_KEYS = frozenset(
    {
        "initial_capital_usd",
        "taker_fee_bps",
        "spread_bps",
        "slippage_bps",
        "participation_slippage_bps_per_pct",
        "max_position_pct",
        "position_fraction",
        "max_bar_participation_pct",
    }
)


@dataclass(frozen=True)
class Fill:
    """Executed bar fill details."""

    qty: float
    price: float
    fee: float
    notional: float
    participation_pct: float
    cost_bps: float


@dataclass(frozen=True)
class ExecutionParams:
    """Normalized execution assumptions for one backtest run."""

    initial_capital: float
    fee_bps: float
    spread_bps: float
    participation_slippage_bps_per_pct: float
    max_position_pct: float
    max_bar_participation_pct: float

    def as_dict(self) -> dict[str, float | str]:
        """Return the public execution config snapshot."""
        return {
            "initial_capital_usd": self.initial_capital,
            "taker_fee_bps": self.fee_bps,
            "spread_bps": self.spread_bps,
            "participation_slippage_bps_per_pct": self.participation_slippage_bps_per_pct,
            "max_position_pct": self.max_position_pct,
            "max_bar_participation_pct": self.max_bar_participation_pct,
            "fill_model": "next_bar_open_spread_plus_participation",
        }


@dataclass
class BacktestState:
    """Mutable execution state for one single-asset spot backtest."""

    cash: float
    position: float = 0.0
    entry_price: float | None = None
    entry_fee_remaining: float = 0.0
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class BacktestResult:
    """Backtest result bundle."""

    result: dict[str, Any]
    trades: list[dict[str, Any]]


def run_bar_backtest(
    candles: list[Candle],
    *,
    strategy_spec: dict[str, Any],
    execution_config: dict[str, Any],
) -> BacktestResult:
    """Run a v1 single-asset spot backtest using next-bar fills."""
    if len(candles) < MIN_BACKTEST_CANDLES:
        msg = "At least five candles are required for a backtest"
        raise ValueError(msg)
    reject_unknown_keys(strategy_spec, STRATEGY_SPEC_KEYS, config_name="strategy_spec")
    reject_unknown_keys(execution_config, EXECUTION_CONFIG_KEYS, config_name="execution_config")

    template = str(strategy_spec.get("template") or "spot_trend_follow")
    parameters = strategy_spec.get("parameters") or {}
    signal = _generate_signal(candles, template=template, parameters=parameters)
    params = _execution_params(execution_config)
    state = BacktestState(cash=params.initial_capital)

    for idx, candle in enumerate(candles):
        close_equity = _record_equity(state, candle)
        if idx >= len(candles) - 1:
            continue
        next_candle = candles[idx + 1]
        target_long = signal[idx] > 0
        if target_long and state.position <= POSITION_EPSILON:
            _enter_long(state, next_candle, close_equity=close_equity, params=params)
        elif not target_long and state.position > POSITION_EPSILON:
            _exit_long(state, next_candle, reference_price=next_candle.open, params=params)

    _close_remaining_position(state, candles[-1], params)

    metric_result = compute_metrics(state.equity_curve, state.trades)
    return BacktestResult(
        result={
            "strategy_template": template,
            "parameters": parameters,
            "execution_config": params.as_dict(),
            "metrics": metric_result.metrics,
            "metric_warnings": metric_result.warnings,
        },
        trades=state.trades,
    )


def reject_unknown_keys(
    config: dict[str, Any], accepted: frozenset[str], *, config_name: str
) -> None:
    """Fail loud on keys the engine would otherwise silently ignore.

    Args:
        config: Caller-supplied config dict.
        accepted: Every key this config honours.
        config_name: Name used in the error, matching the tool argument.

    Raises:
        ValueError: If ``config`` carries any key outside ``accepted``. The
            message names the offending keys and the full accepted set so the
            caller can correct the call rather than guess again.

    """
    unknown = sorted(set(config) - accepted)
    if unknown:
        msg = (
            f"{config_name} has unsupported key(s): {', '.join(unknown)}. "
            f"Accepted keys: {', '.join(sorted(accepted))}."
        )
        raise ValueError(msg)


def _execution_params(execution_config: dict[str, Any]) -> ExecutionParams:
    return ExecutionParams(
        initial_capital=float(
            execution_config.get("initial_capital_usd", DEFAULT_INITIAL_CAPITAL_USD)
        ),
        fee_bps=float(execution_config.get("taker_fee_bps", DEFAULT_TAKER_FEE_BPS)),
        spread_bps=float(
            execution_config.get(
                "spread_bps",
                execution_config.get("slippage_bps", DEFAULT_SPREAD_BPS),
            )
        ),
        participation_slippage_bps_per_pct=float(
            execution_config.get(
                "participation_slippage_bps_per_pct",
                DEFAULT_PARTICIPATION_SLIPPAGE_BPS_PER_PCT,
            )
        ),
        max_position_pct=_normalize_position_pct(
            execution_config.get(
                "max_position_pct",
                execution_config.get("position_fraction", DEFAULT_MAX_POSITION_PCT),
            )
        ),
        max_bar_participation_pct=_normalize_position_pct(
            execution_config.get("max_bar_participation_pct", DEFAULT_MAX_BAR_PARTICIPATION_PCT)
        ),
    )


def _record_equity(state: BacktestState, candle: Candle) -> float:
    close_equity = state.cash + state.position * candle.close
    state.equity_curve.append(
        {
            "timestamp": candle.start.isoformat(),
            "equity": close_equity,
            "cash": state.cash,
            "position": state.position,
            "close": candle.close,
        }
    )
    return close_equity


def _enter_long(
    state: BacktestState,
    candle: Candle,
    *,
    close_equity: float,
    params: ExecutionParams,
) -> None:
    fill = _buy_fill(
        candle,
        target_notional=close_equity * params.max_position_pct,
        available_cash=state.cash,
        fee_bps=params.fee_bps,
        spread_bps=params.spread_bps,
        participation_slippage_bps_per_pct=params.participation_slippage_bps_per_pct,
        max_bar_participation_pct=params.max_bar_participation_pct,
    )
    if fill.qty <= 0:
        return
    state.cash -= fill.notional + fill.fee
    state.position = fill.qty
    state.entry_price = fill.price
    state.entry_fee_remaining = fill.fee
    state.trades.append(_fill_trade(candle, side="BUY", fill=fill, realized_pnl=0.0))


def _exit_long(
    state: BacktestState,
    candle: Candle,
    *,
    reference_price: float,
    params: ExecutionParams,
) -> None:
    position_before = state.position
    fill = _sell_fill(
        candle,
        reference_price=reference_price,
        position=position_before,
        fee_bps=params.fee_bps,
        spread_bps=params.spread_bps,
        participation_slippage_bps_per_pct=params.participation_slippage_bps_per_pct,
        max_bar_participation_pct=params.max_bar_participation_pct,
    )
    if fill.qty <= 0:
        return
    entry_fee_allocated = state.entry_fee_remaining * (fill.qty / position_before)
    realized = (fill.price - (state.entry_price or fill.price)) * fill.qty
    realized -= entry_fee_allocated + fill.fee
    state.cash += fill.notional - fill.fee
    state.trades.append(_fill_trade(candle, side="SELL", fill=fill, realized_pnl=realized))
    state.entry_fee_remaining -= entry_fee_allocated
    state.position -= fill.qty
    if state.position <= POSITION_EPSILON:
        state.position = 0.0
        state.entry_price = None
        state.entry_fee_remaining = 0.0


def _close_remaining_position(
    state: BacktestState,
    last: Candle,
    params: ExecutionParams,
) -> None:
    if state.position > POSITION_EPSILON:
        _exit_long(state, last, reference_price=last.close, params=params)
    state.equity_curve[-1]["equity"] = state.cash + state.position * last.close
    state.equity_curve[-1]["cash"] = state.cash
    state.equity_curve[-1]["position"] = state.position


def _generate_signal(
    candles: list[Candle],
    *,
    template: str,
    parameters: dict[str, Any],
) -> list[int]:
    accepted = TEMPLATE_PARAMETER_KEYS.get(template)
    if accepted is None:
        msg = f"Unsupported v1 crypto strategy template: {template}"
        raise ValueError(msg)
    reject_unknown_keys(
        parameters, accepted, config_name=f"strategy_spec.parameters for {template}"
    )

    closes = np.array([c.close for c in candles], dtype=float)
    if template == "spot_mean_reversion":
        window = int(parameters.get("window", 20))
        z_entry = float(parameters.get("z_entry", -1.0))
        z_exit = float(parameters.get("z_exit", 0.0))
        if window < MIN_MEAN_REVERSION_WINDOW:
            msg = "spot_mean_reversion requires window >= 2"
            raise ValueError(msg)
        return _mean_reversion_signal(closes, window=window, z_entry=z_entry, z_exit=z_exit)
    if template == "spot_trend_follow":
        fast = int(parameters.get("fast_window", 20))
        slow = int(parameters.get("slow_window", 50))
        if fast < 1 or slow < 1 or fast >= slow:
            msg = "spot_trend_follow requires 1 <= fast_window < slow_window"
            raise ValueError(msg)
        return _trend_signal(closes, fast=fast, slow=slow)
    # Reached only if TEMPLATE_PARAMETER_KEYS gains a template that has no
    # branch here, which the guard above cannot catch.
    msg = f"Unsupported v1 crypto strategy template: {template}"
    raise ValueError(msg)


def _normalize_position_pct(value: Any) -> float:
    """Normalize a position cap as a 0..1 fraction.

    User-facing risk configs often express percentages as ``10`` for 10%.
    Execution configs may express the same cap as ``0.10``. Accept both and
    fail closed outside the valid long-only spot range.
    """
    raw = float(value)
    fraction = raw / 100.0 if raw > 1.0 else raw
    if not 0.0 < fraction <= 1.0:
        msg = "max_position_pct must be greater than 0 and no more than 100%"
        raise ValueError(msg)
    return fraction


def _buy_fill(
    candle: Candle,
    *,
    target_notional: float,
    available_cash: float,
    fee_bps: float,
    spread_bps: float,
    participation_slippage_bps_per_pct: float,
    max_bar_participation_pct: float,
) -> Fill:
    base_qty = max(0.0, target_notional / candle.open)
    qty = min(base_qty, _bar_capacity(candle, max_bar_participation_pct))
    if qty <= 0.0:
        return _empty_fill()
    cost_bps = _cost_bps(candle, qty, spread_bps, participation_slippage_bps_per_pct)
    price = candle.open * (1.0 + cost_bps / 10_000.0)
    qty = min(qty, available_cash / (price * (1.0 + fee_bps / 10_000.0)))
    if qty <= 0.0:
        return _empty_fill()
    cost_bps = _cost_bps(candle, qty, spread_bps, participation_slippage_bps_per_pct)
    price = candle.open * (1.0 + cost_bps / 10_000.0)
    notional = qty * price
    fee = notional * fee_bps / 10_000.0
    return Fill(
        qty=qty,
        price=price,
        fee=fee,
        notional=notional,
        participation_pct=_participation_pct(candle, qty),
        cost_bps=cost_bps,
    )


def _sell_fill(
    candle: Candle,
    *,
    reference_price: float,
    position: float,
    fee_bps: float,
    spread_bps: float,
    participation_slippage_bps_per_pct: float,
    max_bar_participation_pct: float,
) -> Fill:
    qty = min(max(0.0, position), _bar_capacity(candle, max_bar_participation_pct))
    if qty <= 0.0:
        return _empty_fill()
    cost_bps = _cost_bps(candle, qty, spread_bps, participation_slippage_bps_per_pct)
    price = reference_price * (1.0 - cost_bps / 10_000.0)
    notional = qty * price
    fee = notional * fee_bps / 10_000.0
    return Fill(
        qty=qty,
        price=price,
        fee=fee,
        notional=notional,
        participation_pct=_participation_pct(candle, qty),
        cost_bps=cost_bps,
    )


def _bar_capacity(candle: Candle, max_bar_participation_pct: float) -> float:
    return max(0.0, candle.volume * max_bar_participation_pct)


def _participation_pct(candle: Candle, qty: float) -> float:
    if candle.volume <= 0.0:
        return 0.0
    return qty / candle.volume


def _cost_bps(
    candle: Candle,
    qty: float,
    spread_bps: float,
    participation_slippage_bps_per_pct: float,
) -> float:
    participation_pct = _participation_pct(candle, qty) * 100.0
    return spread_bps + participation_slippage_bps_per_pct * participation_pct


def _empty_fill() -> Fill:
    return Fill(qty=0.0, price=0.0, fee=0.0, notional=0.0, participation_pct=0.0, cost_bps=0.0)


def _fill_trade(
    candle: Candle,
    *,
    side: str,
    fill: Fill,
    realized_pnl: float,
) -> dict[str, Any]:
    return _trade(
        candle,
        side=side,
        qty=fill.qty,
        price=fill.price,
        fee=fill.fee,
        notional=fill.notional,
        realized_pnl=realized_pnl,
        participation_pct=fill.participation_pct,
        cost_bps=fill.cost_bps,
    )


def _trend_signal(closes: np.ndarray, *, fast: int, slow: int) -> list[int]:
    fast_ma = _rolling_mean(closes, fast)
    slow_ma = _rolling_mean(closes, slow)
    return [
        1 if f is not None and s is not None and f > s else 0
        for f, s in zip(fast_ma, slow_ma, strict=True)
    ]


def _mean_reversion_signal(
    closes: np.ndarray,
    *,
    window: int,
    z_entry: float,
    z_exit: float,
) -> list[int]:
    signals: list[int] = []
    in_position = False
    for idx in range(len(closes)):
        if idx + 1 < window:
            signals.append(0)
            continue
        sample = closes[idx + 1 - window : idx + 1]
        std = float(np.std(sample, ddof=1)) if len(sample) > 1 else 0.0
        z_score = 0.0 if std == 0.0 else (float(closes[idx]) - float(np.mean(sample))) / std
        if not in_position and z_score <= z_entry:
            in_position = True
        elif in_position and z_score >= z_exit:
            in_position = False
        signals.append(1 if in_position else 0)
    return signals


def _rolling_mean(values: np.ndarray, window: int) -> list[float | None]:
    out: list[float | None] = []
    for idx in range(len(values)):
        if idx + 1 < window:
            out.append(None)
        else:
            out.append(float(np.mean(values[idx + 1 - window : idx + 1])))
    return out


def _trade(
    candle: Candle,
    *,
    side: str,
    qty: float,
    price: float,
    fee: float,
    notional: float,
    realized_pnl: float,
    participation_pct: float,
    cost_bps: float,
) -> dict[str, Any]:
    return {
        "timestamp": candle.start.isoformat(),
        "product_id": candle.product_id,
        "side": side,
        "quantity": qty,
        "price": price,
        "fee": fee,
        "notional": notional,
        "realized_pnl": realized_pnl,
        "bar_volume": candle.volume,
        "participation_pct": participation_pct,
        "cost_bps": cost_bps,
    }
