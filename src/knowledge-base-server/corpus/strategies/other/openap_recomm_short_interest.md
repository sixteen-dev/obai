---
entry_type: strategy
id: openap_recomm_short_interest
canonical_name: Analyst Recommendations and Short-Interest
aliases:
- Analyst Recommendations and Short-Interest
- Recomm_ShortInterest
one_line: Cross-sectional equity anomaly that uses Analyst Recommendations and Short-Interest
  to long high-signal stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=4.09 FF+Mom alpha in port sort; reported
  long-short return=1.11, t-stat=4.09.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test recommendation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Analyst Recommendations and Short-Interest
  authors:
  - Drake, Rees
  - Swanson
  year: 2011
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Analyst Recommendations and Short-Interest is represented in the OpenAP signal catalog as a recommendation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Go long firms in lowest quintile of short interest (shortint/shrout) and lowest quintile of analyst recommendations (monthly consensus recommendation using the most recent analyst recommendation in the past 12 months). Go short firms in highest quintile of short interest and highest quintile of analyst recommendations. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Recomm_ShortInterest for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Recomm_ShortInterest; category=recommendation; data=Analyst; evidence=t=4.09 FF+Mom alpha in port sort. Review the generated entry before using it as a final public corpus item.
