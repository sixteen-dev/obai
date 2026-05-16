---
entry_type: strategy
id: openap_customer_momentum
canonical_name: Customer momentum
aliases:
- Customer momentum
- CustomerMomentum
- MomCust
one_line: Cross-sectional equity anomaly that uses Customer momentum to long high-signal
  stocks and short low-signal stocks.
category: momentum
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
- Tab3A is VW, so we do VW. 3B shows works a bit better EW
- 'Original-paper replication evidence: t=3.8 in port sort; reported long-short return=1.578,
  t-stat=3.79.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Customer momentum
  authors:
  - Cohen
  - Frazzini
  year: 2008
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Customer momentum is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Based on firms' principals customers from Compustat Segment data as in Cohen and Frazzini. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CustomerMomentum for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CustomerMomentum; category=lead lag; data=Other; evidence=t=3.8 in port sort. Review the generated entry before using it as a final public corpus item.
