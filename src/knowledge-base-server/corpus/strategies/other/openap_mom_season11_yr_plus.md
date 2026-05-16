---
entry_type: strategy
id: openap_mom_season11_yr_plus
canonical_name: Return seasonality years 11 to 15
aliases:
- MomSeason11YrPlus
- Return seasonality years 11 to 15
one_line: Cross-sectional equity anomaly that uses Return seasonality years 11 to
  15 to long high-signal stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=6.4 in port sort; reported long-short return=0.66,
  t-stat=6.43.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Return seasonality years 11 to 15
  authors:
  - Heston
  - Sadka
  year: 2008
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Return seasonality years 11 to 15 is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average return in the same month over the preceding 11-15 years. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MomSeason11YrPlus for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MomSeason11YrPlus; category=other; data=Price; evidence=t=6.4 in port sort. Review the generated entry before using it as a final public corpus item.
