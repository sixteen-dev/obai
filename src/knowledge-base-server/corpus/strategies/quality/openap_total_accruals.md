---
entry_type: strategy
id: openap_total_accruals
canonical_name: Total accruals
aliases:
- Total accruals
- TotalAccruals
one_line: Cross-sectional equity anomaly that uses Total accruals to long low-signal
  stocks and short high-signal stocks.
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
- Table 8 panel A has regression result with t-stat of 6.38.
- 'Original-paper replication evidence: t=6 in mv reg; reported long-short return=n/a,
  t-stat=6.38.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Total accruals
  authors:
  - Richardson et al.
  year: 2005
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Total accruals is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Before 1988: Change in net working capital ((act - che) - (lct - dlc)) plus change in net noncurrent assets ( (at - act - ivao) - (lt - dlc - dltt)) plus change in net financial assets ( (ivst + ivao - (dltt + dlc + pstk)). Starting in 1988: net income (ni) minus total, operating and investment cashflows (oancf, ivncf, fincf) plus stock sales minus repurchases and dividends (sstk, prstkc, dv)). Scaled by lagged total assets (at). Replace missings in ivao, ivst, dltt, dlc, pstk sstk, prstkc, dv with 0. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute TotalAccruals for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=TotalAccruals; category=investment alt; data=Accounting; evidence=t=6 in mv reg. Review the generated entry before using it as a final public corpus item.
