---
entry_type: strategy
id: openap_aop
canonical_name: Analyst Optimism
aliases:
- AOP
- Analyst Optimism
one_line: Cross-sectional equity anomaly that uses Analyst Optimism to long low-signal
  stocks and short high-signal stocks.
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
- Called OP (optimism) in paper. See AnalystValue.
- 'Original-paper replication evidence: p<0.01 in port sort but nonstandard stats;
  reported long-short return=0.275, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Analyst Optimism
  authors:
  - Frankel
  - Lee
  year: 1998
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Analyst Optimism is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: AnalystValue (defined above) minus IntrinsicValue (defined above), divided by abs(IntrinsicValue). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute AOP for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AOP; category=other; data=Analyst; evidence=p<0.01 in port sort but nonstandard stats. Review the generated entry before using it as a final public corpus item.
