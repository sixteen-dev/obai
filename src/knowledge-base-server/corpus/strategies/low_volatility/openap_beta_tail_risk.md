---
entry_type: strategy
id: openap_beta_tail_risk
canonical_name: Tail risk beta
aliases:
- BetaTailRisk
- Tail risk beta
one_line: Cross-sectional equity anomaly that uses Tail risk beta to long high-signal
  stocks and short low-signal stocks.
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
- Also works VW or monthly
- 'Original-paper replication evidence: Tab4A t-stat 2.48; reported long-short return=0.33,
  t-stat=2.48.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Tail risk beta
  authors:
  - Kelly
  - Jiang
  year: 2014
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Tail risk beta is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Each month, compute the 5th percentile over daily returns over all firms. For all daily return observations with return below that 5th percentile, compute the average of (log(ret/5th percentile of cross-sectional return distribution). Call that average tailEX. BetaTailRisk is the coefficient of a 120-month rolling regression of a firm's stock return on tailEX. Exclude if price less than 5 or share code greater than 11. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BetaTailRisk for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BetaTailRisk; category=risk; data=Price; evidence=Tab4A t-stat 2.48. Review the generated entry before using it as a final public corpus item.
