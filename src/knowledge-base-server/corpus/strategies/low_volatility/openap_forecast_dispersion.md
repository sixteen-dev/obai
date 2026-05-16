---
entry_type: strategy
id: openap_forecast_dispersion
canonical_name: EPS Forecast Dispersion
aliases:
- EPS Forecast Dispersion
- EPSDisp
- ForecastDispersion
one_line: Cross-sectional equity anomaly that uses EPS Forecast Dispersion to long
  low-signal stocks and short high-signal stocks.
category: low_volatility
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=2.9 in port sort; reported long-short return=0.79,
  t-stat=2.88.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test volatility effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: EPS Forecast Dispersion
  authors:
  - Diether, Malloy
  - Scherbina
  year: 2002
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
EPS Forecast Dispersion is represented in the OpenAP signal catalog as a volatility predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Keep fpi = 1 and fpedats > statpers + 30. Standard deviation of earnings estimates (stdev\_est) scaled by mean earnings estimate. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ForecastDispersion for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ForecastDispersion; category=volatility; data=Analyst; evidence=t=2.9 in port sort. Review the generated entry before using it as a final public corpus item.
