---
entry_type: strategy
id: openap_ps
canonical_name: Piotroski F-Score
aliases:
- PS
- Piotroski F-score
- Pscore
one_line: Cross-sectional equity anomaly that uses Piotroski F-score to long high-signal
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
- OP does not explicitly say if it is equal or value weighting, but we find more similar
  results using VW. OP also compounds returns within a year, and rebalances 4 months
  after the end of fiscal year. To approximate, we do portfolio period = 1
- 'Original-paper replication evidence: t=5.59 in port sort nonstandard data lag;
  reported long-short return=1.958333333, t-stat=5.594.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Piotroski F-score
  authors:
  - Piotroski
  year: 2000
  venue: JAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Piotroski F-score is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Sum of nine indicator variables which are: 1 if net income (ib) greater 0; 1 if net cash flow (oancf) greater 0; 1 if return on assets (ib/at) increased relative to previous year; 1 if net cash flow greater net income; 1 if long-term debt to assets (dltt/at) declined over the previous year; if current assets to current liabilities (act/lct) increased over the previous year; 1 if ebit/sale (ebit = ib + txt + xint) increased over the previous year; 1 if revenue to assets increased over the previous year; 1 if shrout $\leq$ shrout last year. Include highest quintile of book-to-market only. Exclude if missing any of the input variables. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PS for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PS; category=composite accounting; data=Accounting; evidence=t=5.59 in port sort nonstandard data lag. Review the generated entry before using it as a final public corpus item.
