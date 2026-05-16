---
entry_type: strategy
id: openap_delcol
canonical_name: Change in current operating liabilities
aliases:
- Change in current operating liabilities
- DelCOL
- LiabCGr
one_line: Cross-sectional equity anomaly that uses Change in current operating liabilities
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
- Table 8, but FMB only. Most results are about accounting rate of return (Table 5-7).
- 'Original-paper replication evidence: t=4.5 in mv reg; reported long-short return=n/a,
  t-stat=4.49.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in current operating liabilities
  authors:
  - Richardson et al.
  year: 2005
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in current operating liabilities is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Difference in current operating liabilities (total current liabilities (lct) minus debt in current liabilities (dlc)) between years t-1 and t, scaled by average total assets (at) in years t-1 and t. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DelCOL for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DelCOL; category=external financing; data=Accounting; evidence=t=4.5 in mv reg. Review the generated entry before using it as a final public corpus item.
