---
entry_type: strategy
id: openap_io_short_interest
canonical_name: Inst own among high short interest
aliases:
- IO_ShortInterest
- Inst own among high short interest
- InstOwnSI
one_line: Cross-sectional equity anomaly that uses Inst own among high short interest
  to long high-signal stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires institutional-holdings (13F) data that the
  OBaI backtest engine does not ingest. Use as routing reference; do not attempt
  backtest execution.
signal_inputs:
- OpenAP 13F data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 5 does not have long short returns in IO variable but shows returns for each
  tercile conditional on short interest. Subjectively large difference between lowest
  and highest IO terciles in alphas for EW returns, but the standard error can be
  large
- 'Original-paper replication evidence: strong port sort but no long-short; reported
  long-short return=0.98, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where 13F data is available, with a monthly
  rebalance workflow and a desire to test ownership effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Inst own among high short interest
  authors:
  - Asquith Pathak
  - Ritter
  year: 2005
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Inst own among high short interest is represented in the OpenAP signal catalog as a ownership predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Exclude all stocks with short interest (ShortInterest) below 99th percentile. IO\_ShortInterest is institutional ownership (instown\_perc). Keep NYSE Only. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute IO_ShortInterest for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IO_ShortInterest; category=ownership; data=13F; evidence=strong port sort but no long-short. Review the generated entry before using it as a final public corpus item.
