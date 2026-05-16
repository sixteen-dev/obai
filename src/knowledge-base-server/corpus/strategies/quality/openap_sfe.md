---
entry_type: strategy
id: openap_sfe
canonical_name: Earnings Forecast to price
aliases:
- EPforecast
- Earnings Forecast to price
- sfe
one_line: Cross-sectional equity anomaly that uses Earnings Forecast to price to long
  high-signal stocks and short low-signal stocks.
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
- Size adjustment and analyst coverage are both extremely important. You can see the
  size adjustment in Table 2.
- 'Original-paper replication evidence: t=5 in long-short size adjusted; reported
  long-short return=1.466666667, t-stat=4.99.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings Forecast to price
  authors:
  - Elgers, Lo
  - Pfeiffer
  year: 2001
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings Forecast to price is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Median estimate for next year eps (fpi = 1) in March, divided by stock price from December. Dec fiscal year ends only, keep only forecasts more than 90 days out. Keep only below median analyst coverage each month. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute sfe for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=sfe; category=valuation; data=Analyst; evidence=t=5 in long-short size adjusted. Review the generated entry before using it as a final public corpus item.
