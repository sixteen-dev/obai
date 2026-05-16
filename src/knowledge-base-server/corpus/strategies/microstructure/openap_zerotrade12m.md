---
entry_type: strategy
id: openap_zerotrade12m
canonical_name: Days with Zero Trades (12 Month)
aliases:
- Days with zero trades
- zerotrade12M
- zerotradeAlt12
one_line: Cross-sectional equity anomaly that uses Days with zero trades to long high-signal
  stocks and short low-signal stocks.
category: microstructure
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Trading data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Also works for 12 or even 24 months.
- 'Original-paper replication evidence: t > 4 in port sort (diff holding periods);
  reported long-short return=0.846, t-stat=4.4.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Days with zero trades
  authors:
  - Liu
  year: 2006
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Days with zero trades is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: In each month, count the number of days with no trades. Define zerotrade as the number of days without trades plus (the sum of monthly turnover (vol/shrout) divided by 48*10$^5$), multiplied by 21/number of trading days per month. Take 12-month average. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute zerotrade12M for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=zerotrade12M; category=liquidity; data=Trading; evidence=t > 4 in port sort (diff holding periods). Review the generated entry before using it as a final public corpus item.
