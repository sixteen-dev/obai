# OBaI Backtest Engine Capability Reference

The drafter uses this to set `engine_fit` and write `approximation_notes`. OBaI-specific. Not derived from the source artifact.

## Supported (engine_fit: native)

- Single-symbol or per-symbol-parallel technical strategies on daily / 1hour / 15min / 5min bars
- Entry / exit rules built from supported operators: greater_than, less_than, crosses_above, crosses_below, equals, not_equals, after_time, before_time
- Indicators: SMA, EMA, RSI, MACD, BB, ATR, ADX, candlestick patterns (CDL_*), VWAP (intraday), statistical (LINEARREG family, STDDEV), dual-input (BETA, CORREL)
- Position sizing: equal_weight, fixed_pct; allocation_mode independent (any timeframe) or portfolio (daily only)
- Risk: stop_loss_pct, take_profit_pct, close_eod, no_entry_after
- Realistic costs: volume_scaled_slippage, estimate_spread
- Walk-forward validation when date range >= 4 years

## Approximable (engine_fit: approximate)

- Cross-sectional ranking strategies → approximate by fixed-universe subset with per-symbol parallel signal
- Factor exposures (value, quality, low-vol) → approximate via hub-provided universe pre-selection plus technical overlay
- Pairs trading → approximate single-pair via correlation / spread indicators on each leg
- Calendar / term-structure spreads → approximate via liquid proxy ETFs (e.g. VXX / VIXY for VIX term structure)

## Not supported (engine_fit: reference_only)

- True dynamic cross-sectional ranking with universe rebalance
- Multi-leg options structures (covered call, wheel, collar, butterfly) — no options chain integration
- Earnings-blackout or event-window logic
- Max holding period (N-bar timed exit)
- Dynamic ATR-trailing stops beyond fixed pct
- Portfolio-level circuit breakers
- Universe selection based on lookahead / future data
- Custom rebalance schedules outside the engine's bar cadence
