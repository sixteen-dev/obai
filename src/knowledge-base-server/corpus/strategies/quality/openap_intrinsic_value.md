---
entry_type: strategy
id: openap_intrinsic_value
canonical_name: Intrinsic or historical value
aliases:
- Intrinsic or historical value
- IntrinsicValue
one_line: Cross-sectional equity anomaly that uses Intrinsic or historical value to
  rank stocks by the signal and form the source-defined long-short spread.
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
- Called V_h in paper or historical earnings based value in paper. Our name comes
  from HXZ, but we should probably rename it.
- 'Original-paper replication evidence: not studied. Ingredient variable.; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Intrinsic or historical value
  authors:
  - Frankel
  - Lee
  year: 1998
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Intrinsic or historical value is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Value based on a two-stage dividend discount model assuming ROE is remains the same as the most recent observation, scaled by market value. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute IntrinsicValue for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IntrinsicValue; category=valuation; data=Accounting; evidence=not studied. Ingredient variable.. Review the generated entry before using it as a final public corpus item.
