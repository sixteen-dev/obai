---
entry_type: strategy
id: openap_iomom_cust
canonical_name: Customers momentum
aliases:
- Customers momentum
- iomom_cust
one_line: Cross-sectional equity anomaly that uses Customers momentum to long high-signal
  stocks and short low-signal stocks.
category: momentum
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
- Early in the paper, they use a stock level mv reg with many controls. Later in the
  paper, they sort industry portfolios instead of stocks. Given that it would have
  been more straightforward to just sort on stocks, I think the evidence on standard
  portfolio sorts is unclear (hence this is a likely predictor, not clear)
- 'Original-paper replication evidence: t=4 in mv reg; reported long-short return=n/a,
  t-stat=4.11.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Other data is available, with a monthly
  rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Customers momentum
  authors:
  - Menzly
  - Ozbas
  year: 2010
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Customers momentum is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: We download BEA Make-Use Tables, match to Compustat by NAICS assuming 5 year lag, compute returns within BEA 70 industries. Then match each industry-year to matched-industry weights corresponding to IO tables as follows: for supplier mom, industry comes from cols of Use table, matched industries from the rows. For customer mom: industry from rows of make table, matched industries from cols. Weights exclude own-industry entries. For each industry-month, average returns of matched industries, weighted using IO tables. Sort industries into deciles based on matched returns, assign firm-months to industry deciles, Finally, long stocks in deciles 8-10 and short decile 1. Drop pre-1986 due to NAICS availability. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute iomom_cust for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=iomom_cust; category=lead lag; data=Other; evidence=t=4 in mv reg. Review the generated entry before using it as a final public corpus item.
