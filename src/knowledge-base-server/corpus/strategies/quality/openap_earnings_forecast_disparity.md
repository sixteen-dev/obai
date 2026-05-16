---
entry_type: strategy
id: openap_earnings_forecast_disparity
canonical_name: Long-vs-short EPS forecasts
aliases:
- EarningsForecastDisparity
- LT_ST_EPS
- Long-vs-short EPS forecasts
one_line: Cross-sectional equity anomaly that uses Long-vs-short EPS forecasts to
  long low-signal stocks and short high-signal stocks.
category: quality
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
- Our strategy is simpler and follows HXZ. OP uses 3x3 sort, then LS corners. OP t-stat
  is 4-factor alpha, but the factor premiums roughly cancel.
- 'Original-paper replication evidence: t=5.1 in LS port; reported long-short return=0.48,
  t-stat=5.08.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Long-vs-short EPS forecasts
  authors:
  - Da
  - Warachka
  year: 2011
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Long-vs-short EPS forecasts is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Analyst forecasted 5-year earnings growth (fgr5yr) minus 100 times the difference between mean earnings forecast (meanest) and fiscal year earnings expectations (fy0a) scaled by the absolute value of fy0a. Drop if fpedats is missing or fpedats - statpers < 30 The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EarningsForecastDisparity for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EarningsForecastDisparity; category=earnings forecast; data=Analyst; evidence=t=5.1 in LS port. Review the generated entry before using it as a final public corpus item.
