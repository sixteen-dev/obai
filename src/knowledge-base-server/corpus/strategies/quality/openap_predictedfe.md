---
entry_type: strategy
id: openap_predictedfe
canonical_name: Predicted Analyst forecast error
aliases:
- EPSforeErr
- Predicted Analyst forecast error
- PredictedFE
one_line: Cross-sectional equity anomaly that uses Predicted Analyst forecast error
  to long low-signal stocks and short high-signal stocks.
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
- Called PErr in OP. Very difficult to understand what OP is doing, so we report something
  close in spirit which produced a consistent port sort (albeit statisticallly insignficiant).
  See also AnalystValue.
- 'Original-paper replication evidence: p<0.01 in reg but nonstandard stats; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Predicted Analyst forecast error
  authors:
  - Frankel
  - Lee
  year: 1998
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Predicted Analyst forecast error is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: The fitted value from cross-sectional regressions of analyst earnings' forecast errors on cross-sectional rankings of 5-year sales growth, book-to-market, AOP, and analyst long term growth. See code for details. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PredictedFE for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PredictedFE; category=earnings forecast; data=Accounting; evidence=p<0.01 in reg but nonstandard stats. Review the generated entry before using it as a final public corpus item.
