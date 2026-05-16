---
entry_type: strategy
id: openap_sin_orig
canonical_name: Sin Stock (original list)
aliases:
- Sin Stock (original list)
- sinOrig
one_line: Cross-sectional equity anomaly that uses Sin Stock (original list) to rank
  stocks by the signal and form the source-defined long-short spread.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires specialized data inputs (short interest, lending
  fees, or other alternative datasets) that the OBaI backtest engine does not ingest.
  Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Other data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- redundant,
- 'Original-paper replication evidence: 9_drop; reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Sin Stock (original list)
  authors:
  - Hong
  - Kacperczyk
  year: 2009
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Sin Stock (original list) is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 0 The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute sinOrig for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=sinOrig; category=other; data=Other; evidence=9_drop. Review the generated entry before using it as a final public corpus item.
