---
entry_type: strategy
id: openap_d_vol_put
canonical_name: Change in put vol
aliases:
- Change in put vol
- dImpVolPut
- dVolPut
one_line: Cross-sectional equity anomaly that uses Change in put vol to long low-signal
  stocks and short high-signal stocks.
category: microstructure
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires options-chain data (implied volatility, open
  interest, put-call ratios) and the OBaI backtest engine has no options-chain integration.
  Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Options data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=2.0 in port sort; reported long-short return=0.42,
  t-stat=2.03.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Options data is available, with a monthly
  rebalance workflow and a desire to test informed trading effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in put vol
  authors:
  - An, Ang, Bali, Cakici
  year: 2014
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in put vol is represented in the OpenAP signal catalog as a informed trading predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using OptionM vol surface, 30 day mat, delta = 0.5, find first diff of implied vol The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute dVolPut for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=dVolPut; category=informed trading; data=Options; evidence=t=2.0 in port sort. Review the generated entry before using it as a final public corpus item.
