---
entry_type: strategy
id: openap_m_rreversal
canonical_name: Medium-run reversal
aliases:
- MRreversal
- Medium-run reversal
- Mom1813
one_line: Cross-sectional equity anomaly that uses Medium-run reversal to long low-signal
  stocks and short high-signal stocks.
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
- We include this to nest MP. Figure 2, two-year line is closest. Table I only shows
  LRreversal. MP cite Jegadeesh and Titman 1993 JF but it's found in De Bondt and
  Thaler 1985 (or something close to it anyway),
- 'Original-paper replication evidence: large ret in similar long-short; reported
  long-short return=0.75, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test long term reversal effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Medium-run reversal
  authors:
  - De Bondt
  - Thaler
  year: 1985
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Medium-run reversal is represented in the OpenAP signal catalog as a long term reversal predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Stock return between months t-18 and t-13. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute MRreversal for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=MRreversal; category=long term reversal; data=Price; evidence=large ret in similar long-short. Review the generated entry before using it as a final public corpus item.
