---
entry_type: strategy
id: openap_pchdepr
canonical_name: Change in depreciation to PPE
aliases:
- Change in depreciation to PPE
- DepGr
- pchdepr
one_line: Cross-sectional equity anomaly that uses Change in depreciation to PPE to
  rank stocks by the signal and form the source-defined long-short spread.
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
  (Table 3). Footnote 6 says depreciation divided by plant assets enters the models
  positively six times and negatively twice.
- 'Original-paper replication evidence: ingredient in complicated model; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in depreciation to PPE
  authors:
  - Holthausen
  - Larcker
  year: 1992
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in depreciation to PPE is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Annual percentage change in the ratio of depreciation (dp) to property, plant and equipment (ppent). The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute pchdepr for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=pchdepr; category=investment alt; data=Accounting; evidence=ingredient in complicated model. Review the generated entry before using it as a final public corpus item.
