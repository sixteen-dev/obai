---
entry_type: strategy
id: openap_depr
canonical_name: Depreciation to PPE
aliases:
- Depreciation to PPE
- depr
one_line: Cross-sectional equity anomaly that uses Depreciation to PPE to rank stocks
  by the signal and form the source-defined long-short spread.
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
- Paper fits a massive model to returns (Table 2), then sorts stocks based on predictions
  (Table 3). Depreciation is mentioned in Footnote 6, which says that depreciation
  enters into the models eight times.
- 'Original-paper replication evidence: ingredient in complicated model; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Depreciation to PPE
  authors:
  - Holthausen
  - Larcker
  year: 1992
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Depreciation to PPE is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Depreciation and amortization (dp) divided by property, plant and equipment net total (ppent). The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute depr for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=depr; category=other; data=Accounting; evidence=ingredient in complicated model. Review the generated entry before using it as a final public corpus item.
