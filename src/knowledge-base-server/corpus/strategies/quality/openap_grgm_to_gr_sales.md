---
entry_type: strategy
id: openap_grgm_to_gr_sales
canonical_name: Gross margin growth to sales growth
aliases:
- GM2SaleGr
- GrGMToGrSales
- Gross margin growth to sales growth
one_line: Cross-sectional equity anomaly that uses Gross margin growth to sales growth
  to long high-signal stocks and short low-signal stocks.
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
- 'Original-paper replication evidence: t=1.9 in mv reg; reported long-short return=n/a,
  t-stat=1.857.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test earnings growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Gross margin growth to sales growth
  authors:
  - Abarbanell
  - Bushee
  year: 1998
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Gross margin growth to sales growth is represented in the OpenAP signal catalog as a earnings growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define gross margin GM as revenue (sale) minus cost of goods sold (cogs). GrGMToGrSales is the percentage growth of GM relative to average GM in years t-1 and t-2, minus the percentage growth of revenue relative to average revenue in years t-1 and t-2. Replace growth rates with growth relative to the previous year only if data for t-2 are not available. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute GrGMToGrSales for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=GrGMToGrSales; category=earnings growth; data=Accounting; evidence=t=1.9 in mv reg. Review the generated entry before using it as a final public corpus item.
