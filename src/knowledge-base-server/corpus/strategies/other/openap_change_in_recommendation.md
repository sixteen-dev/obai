---
entry_type: strategy
id: openap_change_in_recommendation
canonical_name: Change in recommendation
aliases:
- ChRecomm
- Change in recommendation
- ChangeInRecommendation
one_line: Cross-sectional equity anomaly that uses Change in recommendation to long
  high-signal stocks and short low-signal stocks.
category: other
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
- OP sample is 1985-1998 using Zack's, but our IBES recommendations only begins in
  1993. OP is binary, but we follow MP. Even though sample is super short for us,
  it seems to work, and is even mostly monotonic.
- 'Original-paper replication evidence: p<0.01 in LS port, but we lack the data; reported
  long-short return=0.225, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test recommendation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in recommendation
  authors:
  - Jegadeesh et al.
  year: 2004
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in recommendation is represented in the OpenAP signal catalog as a recommendation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: keep last ireccd each month, then average across analysts for each firm-month. Define opscore as 6-ireccd. Signal is opscore - last month's opscore. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChangeInRecommendation for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChangeInRecommendation; category=recommendation; data=Analyst; evidence=p<0.01 in LS port, but we lack the data. Review the generated entry before using it as a final public corpus item.
