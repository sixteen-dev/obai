---
entry_type: strategy
id: openap_patentsrd
canonical_name: Patents to RD expenses
aliases:
- Patents to RD expenses
- PatentsRD
one_line: Cross-sectional equity anomaly that uses Patents to RD expenses to long
  high-signal stocks and short low-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires specialized data inputs (short interest, lending
  fees, or other alternative datasets) that the OBaI backtest engine does not ingest.
  Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Other data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 9 does ff3 style VW to adjust for size. Table 8 shows that predictability
  weak in large firms, so we just focus on small firms and VW to keep this spreadsheet
  manageable.
- 'Original-paper replication evidence: t=4.1 in FF3 style long-short; reported long-short
  return=0.41, t-stat=4.13.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test profitability alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Patents to RD expenses
  authors:
  - Hirschleifer, Hsu
  - Li
  year: 2013
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Patents to RD expenses is represented in the OpenAP signal catalog as a profitability alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Drop obs before 1975. Set expenses for research and development (xrd) and number of patents (npat) to 0 if missing. Calculate Patents to RD as patents in a year divided by $xrd_{t-2} + .8 xrd_{t-3} + .6 xrd_{t-4} + .4xrd_{t-5} + .2 xrd_{t-6}$. Drop if denominator = 0, 2 years or less in Compustat, sic in 6000s, ceq < 0. Double independent sort using (a) size NYSE median (b) terciles of Patents to RD. Long if in small size, highest tercile of Patents to RD, short if in small size, smallest tercile. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PatentsRD for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PatentsRD; category=profitability alt; data=Other; evidence=t=4.1 in FF3 style long-short. Review the generated entry before using it as a final public corpus item.
