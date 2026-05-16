---
entry_type: strategy
id: openap_del_equ
canonical_name: Change in equity to assets
aliases:
- Change in equity to assets
- DelEqu
- Eq2AGr
one_line: Cross-sectional equity anomaly that uses Change in equity to assets to long
  low-signal stocks and short high-signal stocks.
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
- Delta Equity in OP.
- 'Original-paper replication evidence: t=6.3 in mv reg; reported long-short return=n/a,
  t-stat=6.25.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in equity to assets
  authors:
  - Richardson et al.
  year: 2005
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in equity to assets is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Difference in book equity (ceq) between years t-1 and t, scaled by average total assets (at) in years t-1 and t. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DelEqu for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DelEqu; category=investment; data=Accounting; evidence=t=6.3 in mv reg. Review the generated entry before using it as a final public corpus item.
