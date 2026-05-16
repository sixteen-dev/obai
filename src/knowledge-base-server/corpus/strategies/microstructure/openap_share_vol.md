---
entry_type: strategy
id: openap_share_vol
canonical_name: Share Volume
aliases:
- Share Volume
- ShareVol
- VolumeShare
one_line: Cross-sectional equity anomaly that uses Share Volume to long low-signal
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
- OP does a linear regression, but the variable is extremely right skewed, with a
  mean around 5, stdev of 18, and median of 2. To approximate a regression in our
  setup, we long / short depending on the signal's value rather than ranking.
- 'Original-paper replication evidence: t=8.9 in univariate reg; reported long-short
  return=n/a, t-stat=8.86.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test volume effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Share Volume
  authors:
  - Datar, Naik
  - Radcliffe
  year: 1998
  venue: JFM
  url: https://www.openassetpricing.com/data/
---
## Thesis
Share Volume is represented in the OpenAP signal catalog as a volume predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Let tempvol = sum of monthly share trading volume (vol) over the previous three months, scaled by 3 times common shares outstanding (shrout). Let ShareVol = 1 if tempvol > 10%, and ShareVol = 0 if tempvol < 5%. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ShareVol for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ShareVol; category=volume; data=Trading; evidence=t=8.9 in univariate reg. Review the generated entry before using it as a final public corpus item.
