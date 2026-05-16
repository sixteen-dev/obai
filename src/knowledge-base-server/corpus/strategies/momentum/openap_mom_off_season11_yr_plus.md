---
entry_type: strategy
id: openap_mom_off_season11_yr_plus
canonical_name: Off season reversal years 11 to 15
aliases:
- MomOffSeason11YrPlus
- Off season reversal years 11 to 15
one_line: Cross-sectional equity anomaly that uses Off season reversal years 11 to
  15 to long low-signal stocks and short high-signal stocks.
category: momentum
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
- Considering the robustness of this signal, this "small" t-stat may be a fluke, and
  a Bayes-Stein or other multiple testing adjustment could actually increase the t-stat
  magnitude. But of course calling this a likely predictor is a judgment call.
- 'Original-paper replication evidence: t=1.8 in port sort, but similar strats do
  better; reported long-short return=0.19, t-stat=1.77.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Off season reversal years 11 to 15
  authors:
  - Heston
  - Sadka
  year: 2008
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Off season reversal years 11 to 15 is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average return in other months over the preceding 11-15 years. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MomOffSeason11YrPlus for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MomOffSeason11YrPlus; category=other; data=Price; evidence=t=1.8 in port sort, but similar strats do better. Review the generated entry before using it as a final public corpus item.
