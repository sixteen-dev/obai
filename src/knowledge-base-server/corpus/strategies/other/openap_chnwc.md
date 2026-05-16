---
entry_type: strategy
id: openap_chnwc
canonical_name: Change in Net Working Capital
aliases:
- ChNWC
- Change in Net Working Capital
- NWCgr
one_line: Cross-sectional equity anomaly that uses Change in Net Working Capital to
  long low-signal stocks and short high-signal stocks.
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
- Very strong in multivariate regression (t-stat of above 4.6 in both specifications),
  no sorts. Main results (Table 7) use annual fama macbeth.
- 'Original-paper replication evidence: t=4.6 in mv reg; reported long-short return=n/a,
  t-stat=4.61.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in Net Working Capital
  authors:
  - Soliman
  year: 2008
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in Net Working Capital is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Twelve-month change in net working capital. Net working capital is ( (act - che) - (lct - dlc) )/at The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChNWC for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChNWC; category=investment alt; data=Accounting; evidence=t=4.6 in mv reg. Review the generated entry before using it as a final public corpus item.
