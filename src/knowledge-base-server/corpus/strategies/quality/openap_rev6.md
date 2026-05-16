---
entry_type: strategy
id: openap_rev6
canonical_name: Earnings forecast revisions
aliases:
- EPSrevise
- Earnings forecast revisions
- REV6
one_line: Cross-sectional equity anomaly that uses Earnings forecast revisions to
  long high-signal stocks and short low-signal stocks.
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
- Table 5 has portfolio returns, but no-tstats. Tab 7 has large t-stats in regressions.
  Tab5 has monthly rebalancing, but Tab 7's regressiosn use 6-month holding period.
  We find only monthly rebalancing works in port sorts.
- 'Original-paper replication evidence: t=4.1 in regression; reported long-short return=n/a,
  t-stat=4.07.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings forecast revisions
  authors:
  - Chan, Jegadeesh
  - Lakonishok
  year: 1996
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings forecast revisions is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define revisions as the change in the mean earnings estimate (meanest) for the next quarter from month t-1 to t, scaled by stock price in month t-1. REV6 is the sum of that variable from months t-6 to t. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute REV6 for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=REV6; category=earnings forecast; data=Analyst; evidence=t=4.1 in regression. Review the generated entry before using it as a final public corpus item.
