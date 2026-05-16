---
entry_type: strategy
id: openap_volume_trend
canonical_name: Volume Trend
aliases:
- Volume Trend
- VolumeTrend
one_line: Cross-sectional equity anomaly that uses Volume Trend to long low-signal
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
- OP reports mean regression coeff across 90 multiple regressions.
- 'Original-paper replication evidence: t=3 in mv reg nonstandard; reported long-short
  return=n/a, t-stat=3.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test volume effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Volume Trend
  authors:
  - Haugen
  - Baker
  year: 1996
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Volume Trend is represented in the OpenAP signal catalog as a volume predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Rolling coefficient from regressing monthly trading volume on a linear time trend over a window of 60 months (require that at least 30 exist). Scale coefficient by 60-month average of trading volume. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute VolumeTrend for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=VolumeTrend; category=volume; data=Trading; evidence=t=3 in mv reg nonstandard. Review the generated entry before using it as a final public corpus item.
