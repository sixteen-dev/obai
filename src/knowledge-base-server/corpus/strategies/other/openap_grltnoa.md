---
entry_type: strategy
id: openap_grltnoa
canonical_name: Growth in long term operating assets
aliases:
- GrLTNOA
- Growth in long term operating assets
- LTNOAgr
one_line: Cross-sectional equity anomaly that uses Growth in long term operating assets
  to long high-signal stocks and short low-signal stocks.
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
- Long port has t=3.2 by itself, so it's a judgment call.
- 'Original-paper replication evidence: 61 bps spread in long-short; reported long-short
  return=0.61, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Growth in long term operating assets
  authors:
  - Fairfield, Whisenant
  - Yohn
  year: 2003
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Growth in long term operating assets is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Annual growth in net operating assets, minus accruals. Net operating assets are (rect + invt + ppent + aco + intan + ao- ap- lco- lo) / at. Accruals are ( rect-l12.rect + invt - l12.invt + aco - l12.aco - (ap - l12.ap + lco - l12.lco) - dp ) / ((at + l12.at)/2) The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute GrLTNOA for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=GrLTNOA; category=investment; data=Accounting; evidence=61 bps spread in long-short. Review the generated entry before using it as a final public corpus item.
