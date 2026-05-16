---
entry_type: strategy
id: openap_order_backlog_chg
canonical_name: Change in order backlog
aliases:
- Change in order backlog
- OrderBacklogChg
one_line: Cross-sectional equity anomaly that uses Change in order backlog to long
  high-signal stocks and short low-signal stocks.
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
- OP is more aggressive than most and assumes a 4 month lag between fiscal year end
  and data availability. We do the standard 6 month.
- 'Original-paper replication evidence: p<0.01 in port sort; reported long-short return=1.1675,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in order backlog
  authors:
  - Baik
  - Ahn
  year: 2007
  venue: Other
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in order backlog is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define normalized order backlog as order backlog (ob) divided by average total assets (at) in years t-1 and t. Exclude if order backlog is 0. Signal is normalized order backlog minus normalized order backlog one year ago. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute OrderBacklogChg for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OrderBacklogChg; category=accruals; data=Accounting; evidence=p<0.01 in port sort. Review the generated entry before using it as a final public corpus item.
