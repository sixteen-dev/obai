# Strategy JSON Reference

Schema, operands, indicators, and operators for `backtest_run_strategy_tool`,
`backtest_compare_strategies_tool`, `backtest_get_trade_log_tool`, and
`backtest_walk_forward_tool` (all take strategy JSON as a string).

## Strategy JSON Format

This is a shape template, not a recommended parameter set.
- Treat the template below as schema guidance only.
- Do not copy placeholder names, numbers, or field values into the final answer.
- The final `json` block must reflect the actual tested final candidate, not a remembered example.

```json
{
  "name": "<strategy_name>",
  "universe": {
    "symbols": ["<symbol_a>", "<symbol_b>"],
    "benchmark": "<benchmark_symbol>"
  },
  "data_config": {
    "start_date": "<YYYY-MM-DD>",
    "end_date": "<YYYY-MM-DD>",
    "train_end_date": "<YYYY-MM-DD_or_null>",
    "timeframe": "<daily_or_1hour_or_15min_or_5min>"
  },
  "indicators": [
    {
      "id": "<indicator_id>",
      "type": "<supported_indicator>",
      "params": { "<param_name>": "<number>" },
      "source": "<source_field>"
    }
  ],
  "entry_rules": {
    "logic": "<AND_or_OR>",
    "conditions": [
      {
        "left": { "<operand_type>": "<value>" },
        "operator": "<supported_operator>",
        "right": { "<operand_type>": "<value>" }
      }
    ]
  },
  "exit_rules": {
    "logic": "<AND_or_OR>",
    "conditions": []
  },
  "position_sizing": {
    "method": "<equal_weight_or_fixed_pct_or_atr_risk>",
    "max_position_pct": "<number>",
    "max_positions": "<integer>",
    "allocation_mode": "<independent_or_portfolio>",
    "risk_pct": "<number_or_null>"
  },
  "risk_management": {
    "stop_loss_pct": "<number_or_null>",
    "take_profit_pct": "<number_or_null>",
    "close_eod": "<true_or_false>",
    "no_entry_after": "<HH:MM_or_null>",
    "atr_indicator": "<atr_indicator_id_or_null>",
    "stop_atr_multiple": "<number_or_null>",
    "trailing_stop_pct": "<number_or_null>",
    "trailing_stop_atr_multiple": "<number_or_null>",
    "max_holding_bars": "<integer_or_null>",
    "reentry_cooldown_bars": "<integer_or_null>"
  },
  "execution_config": {
    "slippage_pct": "<number>",
    "commission_pct": "<number>",
    "initial_capital": "<number>",
    "volume_scaled_slippage": "<true_or_false>",
    "estimate_spread": "<true_or_false>"
  }
}
```

Field rules:
- `type` must be one of the supported indicators listed below.
- `operator` must be one of the supported operators listed below.
- Multi-output indicators must use the actual output reference name when needed.
- The final emitted JSON must contain real tested values, not placeholder tokens.
- `timeframe` defaults to `"daily"` if omitted. Supported: `daily`, `1hour`, `15min`, `5min`.
- `close_eod` forces position close at session end. Use `true` for day trading strategies.
- `no_entry_after` prevents new entries after a time (e.g., `"15:30"`). Recommended for day trading.
- `atr_indicator` names a declared indicator whose `type` is `ATR`; the ATR stop, ATR trailing stop and `atr_risk` sizing all read that indicator's column.
- `stop_atr_multiple` places the initial stop that multiple of the named ATR indicator's signal-bar value below the fill; it is frozen for the trade and excludes `stop_loss_pct`.
- `atr_risk` buys the whole number of shares whose loss at the ATR stop equals `risk_pct` of equity at the fill, capped by `max_position_pct` and, in portfolio mode, by cash and `max_positions`; it requires `stop_atr_multiple`.
- A trailing stop trails the highest high since entry by a percent of it (`trailing_stop_pct`) or a multiple of the named ATR (`trailing_stop_atr_multiple`), ratchets only upward, is set from the completed bar and checked against the next bar's low, and fills at its level or the worse open.
- `max_holding_bars` exits at the close of the Nth bar held, counting the entry bar as the first; an earlier signal, stop, or target on that bar wins.
- `reentry_cooldown_bars` blocks a new entry in a symbol for that many bars after its last exit.

## Realistic Execution Costs

Two optional flags improve backtest realism by modeling execution costs that vary with
market conditions instead of using flat rates:

- `volume_scaled_slippage: true` — scales slippage by the square root of order
  participation rate (order size / bar volume). Large orders in illiquid stocks pay
  more; small orders in liquid stocks pay less. Base rate is still `slippage_pct`.

- `estimate_spread: true` — estimates bid-ask spread from high-low price data
  (Corwin-Schultz method) and applies half-spread cost on each side of every trade.
  This captures the baseline cost of crossing the spread even for tiny orders.

**When to enable:**
- Default iteration/exploration: leave both `false` (faster, simpler comparison)
- Final validation or production-candidate strategy: enable both
- Comparing strategies across different liquidity profiles: enable both
- User explicitly asks for "realistic costs" or "production-quality backtest": enable both
- Small-cap or illiquid universe: strongly recommend enabling both

**When to keep disabled:**
- Rapid iteration on entry/exit logic (costs add noise to signal comparison)
- Very liquid large-cap-only universes where flat slippage is reasonable

## Condition Operands

Each `left` and `right` in a condition is an **operand** — exactly one of these four types:

| Operand type | JSON format | Use for |
|---|---|---|
| indicator | `{"indicator": "..."}` | Computed indicator columns, raw OHLCV columns (`close`, `open`, `high`, `low`, `volume`), or `benchmark_close` when `universe.benchmark` is set |
| constant | `{"constant": N}` | Fixed numeric thresholds |
| time_of_day | `{"time_of_day": "current"}` | Current bar's time. Only valid value is `"current"`. **Intraday only — errors on daily.** |
| time | `{"time": "HH:MM"}` | Fixed time value (pairs with `after_time`/`before_time`). **Intraday only — errors on daily.** |

**Both `left` and `right` can be any operand type.** The most common patterns:

Shape examples only — not recommended parameters:

**Indicator vs constant** (threshold check):
```json
{"left": {"indicator": "<indicator_id>"}, "operator": "less_than", "right": {"constant": "<threshold>"}}
```

**Indicator vs indicator** (compare price to a computed indicator, or two indicators to each other):
```json
{"left": {"indicator": "close"}, "operator": "greater_than", "right": {"indicator": "<trend_indicator_id>"}}
```
```json
{"left": {"indicator": "<fast_indicator_id>"}, "operator": "crosses_above", "right": {"indicator": "<slow_indicator_id>"}}
```

**Time filter** (intraday session window):
```json
{"left": {"time_of_day": "current"}, "operator": "after_time", "right": {"time": "<HH:MM>"}}
```

**Common mistake**: When the intent is "price above moving average," use indicator vs indicator (`close > <ma_id>`), NOT indicator vs constant (`<ma_id> < 0`). Raw OHLCV columns (`close`, `open`, `high`, `low`) are valid indicator references.

## Supported Indicators

Use `backtest_get_supported_indicators_tool` to discover available indicators, their parameter names with each parameter's accepted range, default and whether it is required, output scale, multi-output fields, the lookback in bars at default parameters, whether the indicator is recursive, and a description. Do not assume indicator parameters or output units from training data. A parameter outside its accepted range, or of the wrong type, is rejected at validation rather than dropped from the run.

An indicator's `source` takes a raw price column or the `id` of any indicator declared before it, so indicators compose: derive a series, then measure it, then reference that measurement. Indicators compute in list order in one pass, so declare each before whatever sources it — a `source` naming an indicator declared later, or a name that is neither a raw column nor a declared indicator, is rejected at validation. When `universe.benchmark` is set, a `source` or a `second_source` may also name `benchmark_close`, the benchmark's close aligned as of each bar, so the same measure can be taken on the benchmark and compared with the symbol's in a rule. Compose this way before concluding a derived quantity cannot be expressed.

### VWAP and Session Anchors

- Session-resetting VWAP: `cumsum(typical_price * volume) / cumsum(volume)`, resetting at each new trading day.
- Requires intraday timeframe (`5min`, `15min`, or `1hour`). Raises a validation error on `daily` timeframe.
- Produces a column (e.g., `vwap_1`) that can be compared against raw price columns in conditions.
- Usage: `close crosses_above vwap_1` for momentum entries, `close less_than vwap_1` for mean-reversion entries.
- No parameters needed, just `"type": "VWAP"` with an id like `"id": "vwap_1"`.
- `AVWAP`: the anchored variant, accumulated from `anchor_date` onward instead of from the session open. Param: `anchor_date`, required and inside the data window; undefined before it. Valid on daily and intraday bars. It anchors one event rather than a rolling reference, so each walk-forward fold whose window does not contain the anchor fails validation and is counted among the failed windows.
- `OPENING_RANGE`: highest high and lowest low of the first `minutes` of each session. Param: `minutes`, which must be a whole multiple of the bar size. Outputs suffixed `_high` and `_low`, undefined until that interval has closed. Intraday only.

### Trend, Volatility and Channels

- `KAMA`: adaptive moving average whose gain follows the efficiency ratio of `source`. Param: `length`. Its smoothing constants are fixed by the library and are not adjustable.
- `NATR`: average true range as a percent of price, so one threshold carries across differently priced symbols. Param: `length`. Reads high/low/close and ignores `source`.
- `PLUS_DI` / `MINUS_DI`: the directional components that pair with `ADX`, which reports strength without direction. Param: `length`.
- `MAX` / `MIN`: highest and lowest value of `source` over the window, including the current bar. Param: `length`. For a channel that excludes the current bar use `DONCHIAN`.
- `DONCHIAN`: channel over the prior bars, excluding the current bar. Param: `length`. Outputs: `upper`, `middle`, `lower`. Reads high/low and ignores `source`.
- `KELTNER`: channel around an EMA of close, set `multiplier` average true ranges wide. Params: `length`, `atr_length`, `multiplier`. Reads high/low/close and ignores `source`. Outputs: `upper`, `middle`, `lower`.
- `BBANDS` also emits `percent_b` and `bandwidth` alongside `upper`, `middle` and `lower`; both are fractions, not percentages.

### Volume and Series Transforms

- `RVOL`: bar volume against the mean volume of the preceding bars, the current bar excluded. Param: `length`. Reads volume and ignores `source`.
- `LAG`: an earlier value of any source. Param: `periods`.

### Candlestick Patterns

- All `CDL_*` patterns are available: `CDL_DOJI`, `CDL_ENGULFING`, `CDL_HAMMER`, `CDL_MORNINGSTAR`, `CDL_EVENINGSTAR`, `CDL_3WHITESOLDIERS`, `CDL_3BLACKCROWS`, `CDL_HARAMI`, `CDL_SHOOTINGSTAR`, `CDL_SPINNINGTOP`, and many more.
- Signal values: `+100` (bullish), `-100` (bearish), `0` (neutral).
- Use with the `equals` operator: `CDL_ENGULFING equals 100` for bullish engulfing, `CDL_ENGULFING equals -100` for bearish.
- Use `not_equals` operator with `0` to detect any signal: `CDL_DOJI not_equals 0`.
- No parameters needed, just `"type": "CDL_ENGULFING"` etc.

### Statistical Indicators

- `LINEARREG`: Linear regression value. Param: `length`.
- `LINEARREG_SLOPE`: Slope of the linear regression. Useful for trend strength. Param: `length`.
- `LINEARREG_ANGLE`: Angle of the linear regression in degrees. Param: `length`.
- `STDDEV`: Standard deviation. Useful for volatility regimes. Param: `length`.
- `ZSCORE`: displacement of `source` from its own window mean, in population standard deviations. Param: `length`.
- `PERCENTILE_RANK`: percentile of the current `source` value against the preceding values, the current bar excluded. Param: `length`. Its `source` may be any earlier indicator id, which is how a volatility or squeeze regime filter is expressed.

### Two-Series Indicators (BETA, CORREL, RATIO, DIFF)

- `BETA`: Beta coefficient between two columns. Params: `length`, `second_source` (column name).
- `CORREL`: Pearson correlation between two columns. Params: `length`, `second_source` (column name).
- `RATIO`: the `source` series divided by `second_source`, unitless. Param: `second_source`, which is required.
- `DIFF`: the `source` series minus `second_source`, in the units of both. Param: `second_source`, which is required.
- Both inputs must be columns within the same symbol's DataFrame. These are NOT cross-symbol indicators, though `second_source` may name `benchmark_close` when `universe.benchmark` is set.
- Example: compute BETA between `close` and a previously computed `sma_50` column by setting `"source": "close"` and `"params": {"length": 20, "second_source": "sma_50"}`.
- The discovery tool reports each two-series indicator's default second series: `BETA` and `CORREL` default to the `high` column, so name an explicit column whenever the intent is a market-relative measure. `RATIO` and `DIFF` have no default and are rejected at validation when `second_source` is omitted.

## Supported Operators

- `greater_than`
- `less_than`
- `crosses_above`
- `crosses_below`
- `equals` — exact equality, useful for candlestick pattern signals (e.g., `CDL_ENGULFING equals 100`)
- `not_equals` — inequality, useful for detecting any candlestick signal (e.g., `CDL_DOJI not_equals 0`)
- `after_time` — bar time >= HH:MM (intraday only)
- `before_time` — bar time < HH:MM (intraday only)
- For range logic, combine `greater_than` and `less_than` with `AND`
- For session-time windows, combine `after_time` and `before_time` with `AND`

## Choosing the right operator from user wording

Map the user's exact wording to the operator below.

| User wording | Operator |
|---|---|
| "drops below X", "is below X", "falls below X", "under X", "< X" | `less_than` |
| "rises above X", "is above X", "exceeds X", "over X", "> X" | `greater_than` |
| "crosses below X", "crossover from above to below", "breaks below X" | `crosses_below` |
| "crosses above X", "crossover from below to above", "breaks above X" | `crosses_above` |
| "equals X", "= X" | `equals` |
| "after HH:MM" | `after_time` |
| "before HH:MM" | `before_time` |

Threshold rule (load-bearing): threshold operators fire on every bar where the condition holds; crossover operators fire only on the bar of transition. Misclassifying a threshold check as a crossover often produces zero trades. Do not promote a threshold to a crossover because the strategy family ("mean reversion") commonly uses crossovers — preserve the user's wording.

## Hypothesis templates

Each template names the behavior it exploits, the rule shape that tests it, the regime in which it should fail, and the engine capabilities it needs. Shapes only: choose lengths and thresholds from the hypothesis and test them; do not copy values from memory.

### Trend breakout with volatility filter

- Hypothesis: a close beyond the recent range continues when the range was quiet before the break.
- Entry shape: `close` `crosses_above` an upper-channel output, AND a volatility indicator `less_than` a threshold, optionally AND a trend-strength indicator `greater_than` a threshold.
- Exit shape: `close` `crosses_below` a shorter trend indicator, or the trailing stop.
- Should fail when: ranging markets, and volatility expansions that reverse.
- Requires: channel from `DONCHIAN` (prior bars only) or `BBANDS`; volatility from `NATR` (percent, so one threshold carries across symbols), `ATR` (price units, so a threshold is per symbol) or `STDDEV`; trend strength `ADX`; risk `stop_atr_multiple` and `trailing_stop_atr_multiple`; sizing `atr_risk`.

### Mean reversion with regime filter

- Hypothesis: short-term oversold readings inside an intact uptrend revert.
- Entry shape: an oscillator `less_than` a threshold, AND `close` `greater_than` a long trend indicator, or AND a trend-strength indicator `less_than` a threshold for a ranging-regime variant.
- Exit shape: the oscillator `greater_than` a threshold, or `close` `crosses_above` the mean indicator, with `max_holding_bars` as the time cap.
- Should fail when: the trend breaks and the dip keeps falling, and in gap-driven regimes.
- Requires: oscillator `RSI`, `WILLR` or `CCI`; trend `SMA` or `EMA`; regime `ADX`; a price displacement in deviations from `ZSCORE`; `max_holding_bars`; `stop_loss_pct` or `stop_atr_multiple`.

### Volume-confirmed momentum

- Hypothesis: price strength accompanied by above-normal participation persists.
- Entry shape: `ROC` `greater_than` a threshold, or `close` `greater_than` a trend indicator, AND a relative-volume indicator `greater_than` a threshold.
- Exit shape: `close` `crosses_below` the trend indicator, or the trailing stop.
- Should fail when: news-driven volume spikes exhaust, and in low-liquidity names where volume is noise.
- Requires: `ROC`; participation from `RVOL`, whose reference is the preceding bars with the current bar excluded; `trailing_stop_pct`; `reentry_cooldown_bars`.

## Intraday Strategy Guidelines

When building strategies with `timeframe` other than `"daily"`:

- **Indicator period adjustment**: SMA(200) on 5-min bars = 200 bars = ~2.5 trading days. For a "200-day SMA equivalent" on 5-min data, use SMA(200 * 78) = SMA(15600). Always think in bars, not days.
- **Relative volume**: `RVOL` on intraday bars measures the bar against the preceding bars, not the same time of day in earlier sessions, so the shape of the session sits inside the value.
- **Use `close_eod: true`** for day trading strategies to force-close all positions at session end.
- **Use `no_entry_after`** to prevent new entries near session close (e.g., `"15:30"` gives 30min to exit before close).
- **Retention limits**: 5-min and 15-min data is limited to 2 years. 1-hour data limited to 5 years. Don't request longer ranges.
- **Time-of-day filters**: Use `after_time`/`before_time` operators with `time_of_day`/`time` operands to restrict entries to specific session windows (e.g., avoid first 15min and last 30min).
- **Holding style**: Report as "intraday" with timeframe in the Strategy Summary. Include `avg_holding_minutes` in Backtest Evidence when available.
- **Trade count**: Intraday strategies produce many more trades per year, so a low count there points to a rule that rarely fires. Trade count is not a significance test: consecutive trades in one symbol overlap and are correlated, so treat any count floor as a screen and state the power limitation in words rather than as a threshold met.
- **Multi-symbol caveat**: The engine backtests each symbol independently and averages equity curves. Portfolio allocation mode (shared capital) is only available for daily timeframes — do not use `allocation_mode: portfolio` with intraday bars.

## Portfolio Allocation Mode

Use `allocation_mode: portfolio` in `position_sizing` only for daily multi-symbol strategies with shared capital. This tracks discrete share quantities and a shared cash pool.

For intraday multi-symbol backtests, use `allocation_mode: independent` (the default). Portfolio mode on `5min`, `15min`, or `1hour` is unsupported and will produce a validation error.

Use `allocation_mode: independent` (default) for per-symbol analysis or when you want isolated signal performance without capital constraints.

Portfolio mode reveals capital-constrained reality: when 5 signals fire but you only have cash for 2, the oldest signals get priority. This produces different (usually more conservative) results than independent mode.

Cross-symbol strategies (sector rotation, pairs trading, ranking) remain unsupported. Portfolio mode is shared capital for independent per-symbol signals.

### Portfolio-specific metrics

When using `allocation_mode: portfolio`, the result includes a `portfolio_metrics` section:
- `capital_utilization_pct`: Approximate deployment level, estimated from position counts (not exact dollar tracking). Treat as a directional indicator, not a precise measure.
- `turnover_rate`: Approximate activity level, computed as total traded notional (entry plus exit) divided by mean equity. It measures capital churned, not profit — use as a relative comparison between strategies, not an absolute figure.
- `position_count_max`: Maximum number of concurrent positions held.
- `position_count_avg`: Average number of concurrent positions held.
- `signals_skipped_count`: Number of entry signals that fired but were not filled (capital limits, cooldown, or an undefined ATR); `signal_diagnostics.entries_skipped_by_reason` splits them.
- `signals_skipped_symbols`: Unique symbols where signals were skipped.

### When to use portfolio mode
- Testing multi-symbol strategies where you want realistic capital allocation behavior.
- Evaluating how capital constraints affect signal capture.
- Comparing the gap between "theoretical" (independent) and "realistic" (portfolio) performance.

### Example daily position_sizing with portfolio mode
```json
"position_sizing": {
  "method": "equal_weight",
  "max_position_pct": 20.0,
  "max_positions": 5,
  "allocation_mode": "portfolio"
}
```
