---
entry_type: strategy
id: openap_citationsrd
canonical_name: Citations to RD expenses
aliases:
- Citations to RD expenses
- CitationsRD
one_line: Cross-sectional equity anomaly that uses Citations to RD expenses to long
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
- 'Original-paper replication evidence: t=2.6 in FF3 style long-short; reported long-short
  return=0.26, t-stat=2.6.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test profitability alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Citations to RD expenses
  authors:
  - Hirschleifer, Hsu
  - Li
  year: 2013
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Citations to RD expenses is represented in the OpenAP signal catalog as a profitability alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Drop obs before 1975. Set expenses for research and development (xrd) and number of citations (ncit) to 0 if missing. Calculate Citations to RD as sum of citations over previous 5 years divided by sum of xrd over years $t-3, \ldots, \t-7$. Drop if denominator = 0, 2 years or less in Compustat, sic in 6000s, ceq < 0. Double independent sort using (a) size NYSE median (b) terciles of Citations to RD. Long if in small size, highest tercile of Citations to RD, short if in small size, smallest tercile. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CitationsRD for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CitationsRD; category=profitability alt; data=Other; evidence=t=2.6 in FF3 style long-short. Review the generated entry before using it as a final public corpus item.
