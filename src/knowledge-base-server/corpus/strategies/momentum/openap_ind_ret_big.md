---
entry_type: strategy
id: openap_ind_ret_big
canonical_name: Industry return of big firms
aliases:
- IndRetBig
- Industry return of big firms
one_line: Cross-sectional equity anomaly that uses Industry return of big firms to
  long high-signal stocks and short low-signal stocks.
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
- 'Table 2 presents a VAR with two states: return of big firms and return of small
  firms. Table 6 is easier to compare to others.'
- 'Original-paper replication evidence: t=11 in mv reg; reported long-short return=n/a,
  t-stat=11.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Industry return of big firms
  authors:
  - Hou
  year: 2007
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Industry return of big firms is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Average monthly return (ret) of the 30% largest companies by market value of equity in the same Fama-French 48 industry. Exclude the largest 30% of companies for IndRetBig (not to compute the anomaly!) The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute IndRetBig for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=IndRetBig; category=lead lag; data=Price; evidence=t=11 in mv reg. Review the generated entry before using it as a final public corpus item.
