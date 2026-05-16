---
entry_type: strategy
id: openap_equity_duration
canonical_name: Equity Duration
aliases:
- Duration
- Equity Duration
- EquityDuration
one_line: Cross-sectional equity anomaly that uses Equity Duration to long low-signal
  stocks and short high-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires firm-level accounting data (balance sheet,
  income statement, cash-flow items) that the OBaI backtest engine does not ingest.
  The engine consumes OHLCV bars on daily/intraday timeframes only. Use as routing
  reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Accounting data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Tab6A uses FF93 style factor (HDMLD). They don't seem to like the factor thing much
  and complain about it on page 14. Our is just VW quintiles for simplicity,
- 'Original-paper replication evidence: t=4.4 in conservative long-short; reported
  long-short return=0.5, t-stat=4.368488219.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Equity Duration
  authors:
  - Dechow, Sloan
  - Soliman
  year: 2004
  venue: RAS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Equity Duration is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: see code The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EquityDuration for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EquityDuration; category=valuation; data=Accounting; evidence=t=4.4 in conservative long-short. Review the generated entry before using it as a final public corpus item.
