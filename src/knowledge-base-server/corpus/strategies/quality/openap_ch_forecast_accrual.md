---
entry_type: strategy
id: openap_ch_forecast_accrual
canonical_name: Change in Forecast and Accrual
aliases:
- ChFAccrual
- ChForecastAccrual
- Change in Forecast and Accrual
one_line: Cross-sectional equity anomaly that uses Change in Forecast and Accrual
  to long high-signal stocks and short low-signal stocks.
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
- Closest is Tab 3B. OP basically does a double sort on accruals and revisions. p-val
  < 0.001, but no t-stat. Our t-stat is enormous, suggesting the p-value is much less
  than 0.001.
- 'Original-paper replication evidence: p-val < 0.001 in port sort; reported long-short
  return=2.375, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in Forecast and Accrual
  authors:
  - Barth
  - Hutton
  year: 2004
  venue: RAS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in Forecast and Accrual is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Within upper half of Accruals distribution, equal to 1 if mean earnings estimate increased relative to the previous month. 0 if it decreased. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChForecastAccrual for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChForecastAccrual; category=earnings forecast; data=Analyst; evidence=p-val < 0.001 in port sort. Review the generated entry before using it as a final public corpus item.
