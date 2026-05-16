---
entry_type: strategy
id: openap_zerotrade1m
canonical_name: Days with Zero Trades (1 Month)
aliases:
- Days with zero trades
- zerotrade1M
- zerotradeAlt1
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
- The 1-month version works for 6 or 12 month holding periods, but not 1 month.
- 'Original-paper replication evidence: t = 3.46 in port sort (12m holding); reported
  long-short return=0.56, t-stat=3.46.'
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
The signal definition is: In each month, count the number of days with no trades. Define zerotrade as the number of days without trades plus (the sum of monthly turnover (vol/shrout) divided by 48*10$^5$), multiplied by 21/number of trading days per month. Take 1-month average. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute zerotrade1M for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=zerotrade1M; category=liquidity; data=Trading; evidence=t = 3.46 in port sort (12m holding). Review the generated entry before using it as a final public corpus item.
