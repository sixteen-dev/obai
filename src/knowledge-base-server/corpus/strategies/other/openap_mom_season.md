---
entry_type: strategy
id: openap_mom_season
canonical_name: Return seasonality years 2 to 5
aliases:
- MomSeason
- Return seasonality years 2 to 5
one_line: Cross-sectional equity anomaly that uses Return seasonality years 2 to 5
  to long high-signal stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Price data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Perhaps one should only include this signal from Heston and Sadka. Certainly one
  shouldn't have 5 flavors of seasonal momentum, and on top of that 5 flavors of the
  off-season. We include all of this to nest HXZ, however. And technically, all of
  these strategies are found in the paper.
- 'Original-paper replication evidence: t=5 in port sort; reported long-short return=0.67,
  t-stat=5.35.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Return seasonality years 2 to 5
  authors:
  - Heston
  - Sadka
  year: 2008
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Return seasonality years 2 to 5 is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average return in the same month over the preceding 2-5 years. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MomSeason for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MomSeason; category=other; data=Price; evidence=t=5 in port sort. Review the generated entry before using it as a final public corpus item.
