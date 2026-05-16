---
entry_type: strategy
id: openap_forecast_dispersionlt
canonical_name: Long-term forecast dispersion
aliases:
- EPSDispLT
- ForecastDispersionLT
- Long-term forecast dispersion
one_line: Cross-sectional equity anomaly that uses Long-term forecast dispersion to
  long high-signal stocks and short low-signal stocks.
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
- OP's focus is on pricing other assets, not the premium on this portfolio. We did
  not study this super in depth.
- 'Original-paper replication evidence: t=1.0 in conservative long-short; reported
  long-short return=0.23, t-stat=0.96.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test volatility effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Long-term forecast dispersion
  authors:
  - Anderson, Ghysels,
  - Juergens
  year: 2005
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Long-term forecast dispersion is represented in the OpenAP signal catalog as a volatility predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Standard deviation of earnings estimates (stdev\_est) scaled by mean earnings estimate. Keep if ME is in the top 500 for the month to approximate S&P 500. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ForecastDispersionLT for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ForecastDispersionLT; category=volatility; data=Analyst; evidence=t=1.0 in conservative long-short. Review the generated entry before using it as a final public corpus item.
