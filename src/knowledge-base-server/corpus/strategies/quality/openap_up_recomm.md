---
entry_type: strategy
id: openap_up_recomm
canonical_name: Up Forecast
aliases:
- Up Forecast
- UpRecomm
one_line: Cross-sectional equity anomaly that uses Up Forecast to long high-signal
  stocks and short low-signal stocks.
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
- We follow MP and measure changes in earnings forecasts, while OP studies changes
  in recommendation.
- 'Original-paper replication evidence: t>8 in 3-day event study; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Up Forecast
  authors:
  - Barber et al.
  year: 2001
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Up Forecast is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Keep fpi = 1. Binary variable equal to 1 if mean earnings forecast (meanest) decreased over the past month. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute UpRecomm for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=UpRecomm; category=earnings forecast; data=Analyst; evidence=t>8 in 3-day event study. Review the generated entry before using it as a final public corpus item.
