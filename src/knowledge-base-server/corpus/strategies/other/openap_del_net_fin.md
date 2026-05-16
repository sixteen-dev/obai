---
entry_type: strategy
id: openap_del_net_fin
canonical_name: Change in net financial assets
aliases:
- Change in net financial assets
- DelNetFin
one_line: Cross-sectional equity anomaly that uses Change in net financial assets
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=6 in unvivariate reg; reported long-short
  return=n/a, t-stat=5.85.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in net financial assets
  authors:
  - Richardson et al.
  year: 2005
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in net financial assets is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Compute the sum of short-term investments (ivst) and investments and advances (ivao) minus the sum of long-term debt (dltt), debt in current liabilities (dlc) and preferred stock capital. Divide the difference between the current and one-year lagged sum by total assets (at) averaged over the current and previous fiscal years. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DelNetFin for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DelNetFin; category=investment alt; data=Accounting; evidence=t=6 in unvivariate reg. Review the generated entry before using it as a final public corpus item.
