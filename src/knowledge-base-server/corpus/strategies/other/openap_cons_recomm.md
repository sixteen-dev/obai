---
entry_type: strategy
id: openap_cons_recomm
canonical_name: Consensus Recommendation
aliases:
- ConsRecomm
- Consensus Recommendation
one_line: Cross-sectional equity anomaly that uses Consensus Recommendation to long
  low-signal stocks and short high-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Main results use daily rebalancing, so we use Table 6C . Portfolio defs are in Tab
  IV caption. Our data only begins in 1993, so it's impossible for us to replicate
  their results (their sample is 1986-1996). However, we get similar results to theirs
  for the 1993-2003 sample.
- 'Original-paper replication evidence: t=3.2 in port sort nonstandard data; reported
  long-short return=0.79, t-stat=3.197.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test recommendation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Consensus Recommendation
  authors:
  - Barber et al.
  year: 2001
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Consensus Recommendation is represented in the OpenAP signal catalog as a recommendation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable if the monthly mean of recommendations (ireccd) over analysts is greater than 3, and 0 if it is less or equal than 1.5. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ConsRecomm for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ConsRecomm; category=recommendation; data=Analyst; evidence=t=3.2 in port sort nonstandard data. Review the generated entry before using it as a final public corpus item.
