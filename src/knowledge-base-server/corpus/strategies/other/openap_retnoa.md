---
entry_type: strategy
id: openap_retnoa
canonical_name: Return on Net Operating Assets
aliases:
- RetNOA
- Return on Net Operating Assets
one_line: Cross-sectional equity anomaly that uses Return on Net Operating Assets
  to long high-signal stocks and short low-signal stocks.
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
- Tab 7 RNOA, insignificant with many controls,
- 'Original-paper replication evidence: t=1.4 in mv reg; reported long-short return=n/a,
  t-stat=1.41.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Return on Net Operating Assets
  authors:
  - Soliman
  year: 2008
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Return on Net Operating Assets is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Net income (ni) over revenue (revt). Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RetNOA for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RetNOA; category=composite accounting; data=Accounting; evidence=t=1.4 in mv reg. Review the generated entry before using it as a final public corpus item.
