---
entry_type: strategy
id: openap_high52
canonical_name: 52 week high
aliases:
- 52 week high
- High52
one_line: Cross-sectional equity anomaly that uses 52 week high to long high-signal
  stocks and short low-signal stocks.
category: momentum
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Price data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 1 shows t=2.00 in LS. Tab 2 shows sign depends entirely on January or non-January,
  and t-stats are huge in each subsample. Should work really well VW, not sure why
  they don't do this. Also works with price filter.
- 'Original-paper replication evidence: t=2.0 in long-short; reported long-short return=0.45,
  t-stat=2.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: 52 week high
  authors:
  - George
  - Hwang
  year: 2004
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
52 week high is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Price (prc/cfacshr) divided by the maximum price over the previous 12 months. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute High52 for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=High52; category=momentum; data=Price; evidence=t=2.0 in long-short. Review the generated entry before using it as a final public corpus item.
