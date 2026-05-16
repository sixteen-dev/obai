---
entry_type: strategy
id: openap_gr_sale_to_gr_receivables
canonical_name: Change in sales vs change in receiv
aliases:
- Change in sales vs change in receiv
- GrSaleToGrReceivables
- RevG2ARG
one_line: Cross-sectional equity anomaly that uses Change in sales vs change in receiv
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
- 'Original-paper replication evidence: t=1.6 in mv reg; reported long-short return=n/a,
  t-stat=1.622.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in sales vs change in receiv
  authors:
  - Abarbanell
  - Bushee
  year: 1998
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in sales vs change in receiv is represented in the OpenAP signal catalog as a profitability alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Percentage growth in sales (sale) relative to average sales of t-1 and t-2, minus percentage growth in receivables (rect) relative to average receivables of t-1 and t-2. Both growth terms are calculated relative to t-1 only if t-2 is missing. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute GrSaleToGrReceivables for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=GrSaleToGrReceivables; category=profitability alt; data=Accounting; evidence=t=1.6 in mv reg. Review the generated entry before using it as a final public corpus item.
