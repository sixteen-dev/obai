---
entry_type: strategy
id: openap_trend_factor
canonical_name: Trend Factor
aliases:
- Trend Factor
- TrendFactor
one_line: Cross-sectional equity anomaly that uses Trend Factor to long high-signal
  stocks and short low-signal stocks.
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
- Filters (exchd in 1,2,3; sharecd in 10, 11; abs(prc)>5; mve>lowest decile of NYSE
  breakpoint distribution) are imposed at the signal stage in order to run cross-sectional
  regressions on the correct sample
- 'Original-paper replication evidence: t=15.0 in port sort; reported long-short return=1.63,
  t-stat=15.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Trend Factor
  authors:
  - Han, Zhou, Zhu
  year: 2016
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Trend Factor is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: See paper section 2.1 and 2.2 The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute TrendFactor for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=TrendFactor; category=momentum; data=Price; evidence=t=15.0 in port sort. Review the generated entry before using it as a final public corpus item.
