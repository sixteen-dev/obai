---
entry_type: strategy
id: openap_conv_debt
canonical_name: Convertible debt indicator
aliases:
- ConvDebt
- Convertible debt indicator
one_line: Cross-sectional equity anomaly that uses Convertible debt indicator to long
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
- Table 4, DCONV has FM regression results for dummy. Dummy has opposite sign of covertible
  proportion, both are significant. Table 5C has port sort for proprotion, and returns
  are nonmonotonic and LS t=1.7. We focus on the dummy but it's a judgement call.
- 'Original-paper replication evidence: t > 2.6 in mv reg; reported long-short return=n/a,
  t-stat=4.5.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Convertible debt indicator
  authors:
  - Valta
  year: 2016
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Convertible debt indicator is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Binary variable equal to 1 if deferred charges (dc) greater than 0 or common shares reserved for convertible debt (cshrc) greater than 0. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ConvDebt for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ConvDebt; category=external financing; data=Accounting; evidence=t > 2.6 in mv reg. Review the generated entry before using it as a final public corpus item.
