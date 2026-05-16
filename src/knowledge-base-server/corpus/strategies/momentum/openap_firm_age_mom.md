---
entry_type: strategy
id: openap_firm_age_mom
canonical_name: Firm Age - Momentum
aliases:
- Firm Age - Momentum
- FirmAgeMom
- MomYoung
one_line: Cross-sectional equity anomaly that uses Firm Age - Momentum to long high-signal
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
- Returns are monotonic in momentum, suggesting this variable can be continuous.
- 'Original-paper replication evidence: t = 7.21 in long portfolio; reported long-short
  return=2.9, t-stat=7.21.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Firm Age - Momentum
  authors:
  - Zhang
  year: 2006
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Firm Age - Momentum is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 6 month return, restricted to the bottom quintile of the cross-sectional firm age distribution. Exclude if price less than 5 or firm younger than 12 months. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute FirmAgeMom for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=FirmAgeMom; category=momentum; data=Price; evidence=t = 7.21 in long portfolio. Review the generated entry before using it as a final public corpus item.
