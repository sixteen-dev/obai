---
entry_type: strategy
id: openap_inv_growth
canonical_name: Inventory Growth
aliases:
- InvGrowth
- InvenGr
- Inventory Growth
one_line: Cross-sectional equity anomaly that uses Inventory Growth to long low-signal
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=6.6 in port sort; reported long-short return=0.89,
  t-stat=6.64.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Inventory Growth
  authors:
  - Belo
  - Lin
  year: 2012
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Inventory Growth is represented in the OpenAP signal catalog as a profitability predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Defate invt growth using gnp deflator. Signal is deflated invt growth rate from fiscal year t to fiscal year t-1. Drop if 1 digit sic code is 4 or 6, or if at or ppent <= 0 The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute InvGrowth for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=InvGrowth; category=profitability; data=Accounting; evidence=t=6.6 in port sort. Review the generated entry before using it as a final public corpus item.
