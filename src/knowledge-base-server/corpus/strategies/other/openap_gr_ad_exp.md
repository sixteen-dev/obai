---
entry_type: strategy
id: openap_gr_ad_exp
canonical_name: Growth in advertising expenses
aliases:
- AdExpGr
- GrAdExp
- Growth in advertising expenses
one_line: Cross-sectional equity anomaly that uses Growth in advertising expenses
  to long low-signal stocks and short high-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires firm-level accounting data (balance sheet,
  income statement, cash-flow items) that the OBaI backtest engine does not ingest.
  The engine consumes OHLCV bars on daily/intraday timeframes only. Use as routing
  reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Accounting data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 2, panel A , 10-1. Portfolios are "rebalanced every month using the most recent
  advertising spending data"
- 'Original-paper replication evidence: t=3.5 in long-short; reported long-short return=0.58,
  t-stat=3.54.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Growth in advertising expenses
  authors:
  - Lou
  year: 2014
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Growth in advertising expenses is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Log of advertising expense (xad) minus log of advertising expense last year. Exclude if price less than 5, xad less than .1 or stock in the lowest decile of market value of equity. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute GrAdExp for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=GrAdExp; category=investment alt; data=Accounting; evidence=t=3.5 in long-short. Review the generated entry before using it as a final public corpus item.
