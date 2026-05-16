---
entry_type: strategy
id: openap_order_backlog
canonical_name: Order backlog
aliases:
- Order backlog
- OrderBacklog
one_line: Cross-sectional equity anomaly that uses Order backlog to long low-signal
  stocks and short high-signal stocks.
category: other
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
- Table 3, but FMB size adjusted only. Other tables use nonlinear regressions.
- 'Original-paper replication evidence: t=2.38 in univariate size-adjusted FMB; reported
  long-short return=n/a, t-stat=2.38.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test sales growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Order backlog
  authors:
  - Rajgopal, Shevlin, Venkatachalam
  year: 2003
  venue: RAS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Order backlog is represented in the OpenAP signal catalog as a sales growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Order backlog (ob) divided by average total assets (at) in years t-1 and t. Exclude if order backlog is 0. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute OrderBacklog for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OrderBacklog; category=sales growth; data=Accounting; evidence=t=2.38 in univariate size-adjusted FMB. Review the generated entry before using it as a final public corpus item.
