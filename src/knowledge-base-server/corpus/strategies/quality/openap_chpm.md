---
entry_type: strategy
id: openap_chpm
canonical_name: Change in Profit Margin
aliases:
- ChPM
- Change in Profit Margin
- PMGrowth
one_line: Cross-sectional equity anomaly that uses Change in Profit Margin to long
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
- Tab 7 Delta PM, insignificant with many controls, no sorts.
- 'Original-paper replication evidence: t=0.3 in mv reg; reported long-short return=n/a,
  t-stat=0.33.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test sales growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in Profit Margin
  authors:
  - Soliman
  year: 2008
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in Profit Margin is represented in the OpenAP signal catalog as a sales growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Annual change in profit margin PM (profit margin defined below). Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChPM for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChPM; category=sales growth; data=Accounting; evidence=t=0.3 in mv reg. Review the generated entry before using it as a final public corpus item.
