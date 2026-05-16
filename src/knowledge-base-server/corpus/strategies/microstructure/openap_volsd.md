---
entry_type: strategy
id: openap_volsd
canonical_name: Volume Variance
aliases:
- VolSD
- Volume Variance
- VolumeSD
one_line: Cross-sectional equity anomaly that uses Volume Variance to long low-signal
  stocks and short high-signal stocks.
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
- CVVOL and VOL in OP. Tab 3B has port sort but no LS or t-stats. Tab 5B has FM reg
- 'Original-paper replication evidence: t=3.6 in regression; reported long-short return=n/a,
  t-stat=3.56.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Volume Variance
  authors:
  - Chordia, Subra, Anshuman
  year: 2001
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Volume Variance is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Rolling standard deviation of monthly trading volume (vol) over the past 36 months (require at least 24 observations). Include only NYSE stocks. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute VolSD for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=VolSD; category=liquidity; data=Trading; evidence=t=3.6 in regression. Review the generated entry before using it as a final public corpus item.
