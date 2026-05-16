---
entry_type: strategy
id: openap_cash
canonical_name: Cash to assets
aliases:
- Cash
- Cash to assets
one_line: Cross-sectional equity anomaly that uses Cash to assets to long high-signal
  stocks and short low-signal stocks.
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
- Table 4 has long-short returns with t-stat of 2.14 for raw EW. Much stronger after
  factor adjustments. Other tables show double sorts, alphas, Table 4, Panel A, column
  DeltaCH
- 'Original-paper replication evidence: t=2.14 in port sort but strong with adjustments;
  reported long-short return=0.69, t-stat=2.14.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test asset composition effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Cash to assets
  authors:
  - Palazzo
  year: 2012
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Cash to assets is represented in the OpenAP signal catalog as a asset composition predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Ratio of quarterly cash and short-term investments (cheq) and total assets (atq). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Cash for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Cash; category=asset composition; data=Accounting; evidence=t=2.14 in port sort but strong with adjustments. Review the generated entry before using it as a final public corpus item.
