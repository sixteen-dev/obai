---
entry_type: strategy
id: openap_pos_neg_cons
canonical_name: Pos vs negative consistent return
aliases:
- Pos vs negative consistent return
- PosNegCons
one_line: Cross-sectional equity anomaly that uses Pos vs negative consistent return
  to long high-signal stocks and short low-signal stocks.
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
- Not in either MP or HXZ and not top 3,
- 'Original-paper replication evidence: 9_drop; reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Pos vs negative consistent return
  authors:
  - Watkins
  year: 2003
  venue: Journal of Behavioral Finance
  url: https://www.openassetpricing.com/data/
---
## Thesis
Pos vs negative consistent return is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary signal equal to if ConsPosRet equal to 1, and equal to 0 if CosnNegRet equal to 1. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PosNegCons for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PosNegCons; category=momentum; data=Price; evidence=9_drop. Review the generated entry before using it as a final public corpus item.
