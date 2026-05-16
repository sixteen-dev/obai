---
entry_type: strategy
id: openap_oper_profrd
canonical_name: Operating profitability R&D adjusted
aliases:
- OperProfRD
- Operating profitability R&D adjusted
one_line: Cross-sectional equity anomaly that uses Operating profitability R&D adjusted
  to long high-signal stocks and short low-signal stocks.
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
- This is operating prof with an R&D adjustment. Strong in FM reg with controls, but
  insignificant in VW port sorts (Tab 4). OP states they lag denominator, but 2015
  JFE does not lag, no clear motivation to lag, and our replications much closer to
  OP without lag.
- 'Original-paper replication evidence: t=1.8 in port sort; reported long-short return=0.29,
  t-stat=1.84.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Operating profitability R&D adjusted
  authors:
  - Ball et al.
  year: 2016
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Operating profitability R&D adjusted is represented in the OpenAP signal catalog as a profitability predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Revenue (revt) minus cost (cogs) - (administrative expenses (xsga) - R&D expenses (xrd)), all divided by total assets (at) in year t. Replace all variables in the numerator with 0 if they are missing. Exclude if share code is greater 11, market value of equity, BM or total assets are missing, or if SIC code between 6000 and 6999. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute OperProfRD for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OperProfRD; category=profitability; data=Accounting; evidence=t=1.8 in port sort. Review the generated entry before using it as a final public corpus item.
