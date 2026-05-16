---
entry_type: strategy
id: openap_excl_exp
canonical_name: Excluded Expenses
aliases:
- ExclExp
- ExcludExp
- Excluded Expenses
one_line: Cross-sectional equity anomaly that uses Excluded Expenses to long low-signal
  stocks and short high-signal stocks.
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
- Called total excusions in paper. LS return is not at all monotonic, but reg (tab
  5) is extremely significant. Tab 7 port sort has a nonstandard p-value and subsets
  data, so we hand collect tab 5.
- 'Original-paper replication evidence: t=5.7 in mv reg; reported long-short return=n/a,
  t-stat=6.78.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Excluded Expenses
  authors:
  - Doyle, Lundholm
  - Soliman
  year: 2003
  venue: RAS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Excluded Expenses is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Difference between unadjusted earnings (EPSActualUnadj) from IBES and quarterly earnings per share (epspiq). Exclude the highest and lowest 1% of values. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ExclExp for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ExclExp; category=composite accounting; data=Analyst; evidence=t=5.7 in mv reg. Review the generated entry before using it as a final public corpus item.
