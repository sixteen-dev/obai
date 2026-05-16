---
entry_type: strategy
id: openap_ebm
canonical_name: Enterprise Component of BM
aliases:
- BMent
- EBM
- Enterprise component of BM
one_line: Cross-sectional equity anomaly that uses Enterprise component of BM to long
  high-signal stocks and short low-signal stocks.
category: quality
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
- 'Enterprise = operating = NOA in OP. So this should be NOA/P^NOA. Need to adjust
  sample dates: even though paper says it begins in 1962, there is only 1 stock for
  the first 5 months of 1962. Table 4a has double sort with most t-stats above 3.
  hand t-stat is 3.0 for simplicity and hand ret is 0.12. OP drops extreme obs but
  we don''t.'
- 'Original-paper replication evidence: t=3.0 in double sort; reported long-short
  return=0.12, t-stat=3.0.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Enterprise component of BM
  authors:
  - Penman, Richardson
  - Tuna
  year: 2007
  venue: JAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Enterprise component of BM is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: (ceq + che - dltt - dlc - dc - dvpa+ tstkp ) / (mve\_c + che - dltt - dlc - dc - dvpa+ tstkp). Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EBM for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EBM; category=valuation; data=Accounting; evidence=t=3.0 in double sort. Review the generated entry before using it as a final public corpus item.
