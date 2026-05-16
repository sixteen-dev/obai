---
entry_type: strategy
id: openap_mom_rev
canonical_name: Momentum and LT Reversal
aliases:
- MomRev
- Momentum and LT Reversal
one_line: Cross-sectional equity anomaly that uses Momentum and LT Reversal to long
  high-signal stocks and short low-signal stocks.
category: momentum
asset_classes:
- equities
typical_holding_period: quarterly
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
- 'Original-paper replication evidence: t=4.3 in long-short; reported long-short return=0.48,
  t-stat=4.29.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Momentum and LT Reversal
  authors:
  - Chan
  - Ko
  year: 2006
  venue: JOIM
  url: https://www.openassetpricing.com/data/
---
## Thesis
Momentum and LT Reversal is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable equal to 1 if firm is in the highest Mom6m quintile and the lowest Mom36m quintile, and equal to 0 if firm is in the lowest Mom6m quintile and the highest Mom36m quintile. Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MomRev for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MomRev; category=momentum; data=Price; evidence=t=4.3 in long-short. Review the generated entry before using it as a final public corpus item.
