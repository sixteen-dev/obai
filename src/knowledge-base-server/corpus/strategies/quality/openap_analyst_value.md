---
entry_type: strategy
id: openap_analyst_value
canonical_name: Analyst Value
aliases:
- Analyst Value
- AnalystValue
one_line: Cross-sectional equity anomaly that uses Analyst Value to long high-signal
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
- Usually called V_f/P or V_f in OP. OP finds p-val < 0.01, but spread is only 26
  bps and sample is only 15 years long. Also, p-value comes "is derived using Monte
  Carlo simulation. Specifically, we form empirical reference distributions by randomly
  assigning eligible firms into quintiles each year." Not sure OP's random assignment
  turned out right, since standard p-values are much higher than theirs for their
  Book-to-Price portfolios, which should be easy to replicate. In the end, we judge
  predictability in OP as likely, since the nonstandard p-value, small return spread,
  and short sample suggest our test may only be borderline.
- 'Original-paper replication evidence: p<0.01 in port sort but nonstandard stats;
  reported long-short return=0.258333333, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Analyst Value
  authors:
  - Frankel
  - Lee
  year: 1998
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Analyst Value is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Value based on a three-stage dividend discount model and analyst forecasts, scaled by market value. See code for details. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute AnalystValue for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AnalystValue; category=valuation; data=Analyst; evidence=p<0.01 in port sort but nonstandard stats. Review the generated entry before using it as a final public corpus item.
