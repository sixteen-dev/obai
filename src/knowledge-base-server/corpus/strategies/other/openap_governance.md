---
entry_type: strategy
id: openap_governance
canonical_name: Governance Index
aliases:
- Governance
- Governance Index
one_line: Cross-sectional equity anomaly that uses Governance Index to long low-signal
  stocks and short high-signal stocks.
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
- Tab7 shows value-weighted FF alpha t-stat of 2.73. No loadings provided. Table 6
  shows portfolio FF3 regs, and that both SMB and HML loadings contribute to spread,
  so it's not super clear if the raw LS will be significant. Port sort is not very
  monotonic.
- 'Original-paper replication evidence: t=2.7 in long short FF3 alpha; reported long-short
  return=0.72, t-stat=2.769230769.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Governance Index
  authors:
  - Gompers, Ishii
  - Metrick
  year: 2003
  venue: QJE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Governance Index is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Index available from http://faculty.som.yale.edu/andrewmetrick/data.html . The index is only available every 2-3 years for each firm, we replace intermediate missing values with the latest available one. Value-weighted. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Governance for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Governance; category=other; data=Other; evidence=t=2.7 in long short FF3 alpha. Review the generated entry before using it as a final public corpus item.
