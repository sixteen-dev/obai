# Completed daily-bar signal input

`scripts.evaluate_signals` evaluates entry/exit predicates from the actual
tested strategy JSON. It does not download data, compute indicators, size
orders or manage positions. A positive predicate is not deployment approval.
The host's adapter must implement and verify every remaining strategy feature.

Use the exchange calendar to determine the last completed session and its
previous session. Verify both records correspond to those sessions, not two
arbitrary historical bars. Exclude today's unfinished daily bar. Preserve the
tool request, source, retrieval time, period, adjustment basis and enough
history to verify warm-up. Compare indicator values and decisions against
backtest fixtures: matching indicator names alone does not establish matching
calculations. Mark `warmup_complete` only after that check.

Normalize a verified snapshot as below (illustrative values, not market data):

```json
{
  "symbol": "AAPL",
  "timeframe": "daily",
  "price_basis": "validated-plan-basis",
  "warmup_complete": true,
  "previous": {
    "date": "2026-09-09", "complete": true,
    "values": {"rsi14": 29, "close": 200}
  },
  "current": {
    "date": "2026-09-10", "complete": true,
    "values": {"rsi14": 30, "close": 201}
  }
}
```

`values` keys must match the strategy's indicator IDs/raw OHLCV operands.
Supply both sides of a crossover on both dates. The supported live indicator
types are RSI/SMA/EMA/WMA/DEMA/TEMA/ADX with `length` and the provider's
standard source; composed sources and other indicators require another
validated adapter. `length` is the backtest schema's parameter name, which the
frozen strategy JSON carries; `period` is the live market-data tool's argument
for the same lookback, so translate it when requesting values.
Supported comparisons: `greater_than`, `less_than`,
`equals`, `not_equals`, `crosses_above`, `crosses_below`, under AND/OR logic.
Like the equity engine, a crossover requires a strictly opposite previous
value and a current value touching or passing the threshold: 29 → 30 crosses
above 30; 30 → 31 does not. Empty rule sets produce no signal.

```bash
uv run python -m scripts.evaluate_signals \
  --strategy memory/strategies/candidate.json \
  --snapshot memory/snapshots/AAPL.json \
  --expected-bar-date "$COMPLETED_SESSION_DATE" \
  --expected-price-basis "$VALIDATED_PRICE_BASIS"
```

The expected date/basis come from the calendar and saved execution plan,
not from the potentially stale snapshot itself. Quality flags are upstream
attestations, not proof that the helper checked a provider's calculations.
Nonzero exit means no usable signal. Reconcile managed position state and
pending orders before applying predicates; exits, protection, sizing, capital
cap, holding limits and cooldowns remain the execution adapter's responsibility.
