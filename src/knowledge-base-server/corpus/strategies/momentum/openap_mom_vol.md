---
entry_type: strategy
id: openap_mom_vol
canonical_name: Momentum in high volume stocks
aliases:
- MomVol
- Momentum in high volume stocks
one_line: Cross-sectional equity anomaly that uses Momentum in high volume stocks
  to long high-signal stocks and short low-signal stocks.
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
- We use monthly instead of daily volume.
- 'Original-paper replication evidence: t=6 in long-short, lots of robustness; reported
  long-short return=1.55, t-stat=5.78.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Momentum in high volume stocks
  authors:
  - Lee
  - Swaminathan
  year: 2000
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Momentum in high volume stocks is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define momentum as Mom6m, and volume as the rolling average of the past 6 months of monthly turnover (minimum 5 months). Independent sort stocks into 10 momentum ports and 3 volume ports. Keep if volume is in the top port, and assign signal = momentum port. Drop if less than 2 years on CRSP. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MomVol for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MomVol; category=momentum; data=Price; evidence=t=6 in long-short, lots of robustness. Review the generated entry before using it as a final public corpus item.
