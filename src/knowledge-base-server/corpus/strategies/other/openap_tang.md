---
entry_type: strategy
id: openap_tang
canonical_name: Tangibility
aliases:
- Tangibility
- tang
one_line: Cross-sectional equity anomaly that uses Tangibility to long high-signal
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
- Paper is very painful to read. Defines tangibility using formula we follow and used
  by Almeida and Campello. Measures financial constraints using either asset size,
  payout ratio, etc, with bottom 30% classified as constrained. Table 4 finds that
  tangibility only predicts returns in constrained firms in fm reg. Table 7 forms
  FF3-style LS ports using only constrained firms and finds mixed results. We follow
  Table 4.
- 'Original-paper replication evidence: t=3.37 in univariate FMB; reported long-short
  return=n/a, t-stat=3.37.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test asset composition effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Tangibility
  authors:
  - Hahn
  - Lee
  year: 2009
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Tangibility is represented in the OpenAP signal catalog as a asset composition predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Cash and short-term investments (che) plus .715*receivables (rect) + .547*inventory (invt) + .535* property, plant and equipment (ppent), scaled by total assets (at). Only defined for manufacturing firms (SIC $\geq$ 2000 and SIC <4000). Exclude the lowest tercile of manufacturing firms by total assets. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute tang for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=tang; category=asset composition; data=Accounting; evidence=t=3.37 in univariate FMB. Review the generated entry before using it as a final public corpus item.
