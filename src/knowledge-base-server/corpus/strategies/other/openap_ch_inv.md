---
entry_type: strategy
id: openap_ch_inv
canonical_name: Change in Inventory
aliases:
- ChInv
- Inventory Growth
- Invntory
one_line: Cross-sectional equity anomaly that uses Inventory Growth to long low-signal
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
- Table 1 \Delta Invent row has decile hedge size adjusted returns. T-stats are missing
  though. Main results use panel regression coefficient and stars.
- 'Original-paper replication evidence: t>2.6 in port sort; reported long-short return=0.949166667,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Inventory Growth
  authors:
  - Thomas
  - Zhang
  year: 2002
  venue: RAS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Inventory Growth is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 12 month change in inventory (invt) divided by average total assets. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChInv for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChInv; category=investment alt; data=Accounting; evidence=t>2.6 in port sort. Review the generated entry before using it as a final public corpus item.
