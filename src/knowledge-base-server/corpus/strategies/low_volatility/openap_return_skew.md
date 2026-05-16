---
entry_type: strategy
id: openap_return_skew
canonical_name: Return skewness
aliases:
- RetSkew
- Return skewness
- ReturnSkew
one_line: Cross-sectional equity anomaly that uses Return skewness to long low-signal
  stocks and short high-signal stocks.
category: low_volatility
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
- OP finds negative and significant relationship EW, and positive and significant
  relationship VW. They conclude that results are "difficult to interpret."
- 'Original-paper replication evidence: t=4 in port sort; reported long-short return=0.47,
  t-stat=4.01.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Return skewness
  authors:
  - Bali, Engle
  - Murray
  year: 2015
  venue: Book
  url: https://www.openassetpricing.com/data/
---
## Thesis
Return skewness is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Skewness of daily returns (ret) over previous month. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ReturnSkew for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ReturnSkew; category=risk; data=Price; evidence=t=4 in port sort. Review the generated entry before using it as a final public corpus item.
