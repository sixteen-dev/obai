---
entry_type: strategy
id: openap_kz
canonical_name: Kaplan Zingales index
aliases:
- KZ
- Kaplan Zingales index
one_line: Cross-sectional equity anomaly that uses Kaplan Zingales index to long low-signal
  stocks and short high-signal stocks.
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
- Most of the paper is about covariance analysis. OP does VW with 3x3 FF3 style even
  though EW gets huge t-stats. We just do VW. Called FC in paper. Table 5 has factor
  returns.
- 'Original-paper replication evidence: t=1.1 in conservative port sort; reported
  long-short return=0.13, t-stat=1.102326615.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test composite accounting effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Kaplan Zingales index
  authors:
  - Lamont, Polk
  - Saa-Requejo
  year: 2001
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Kaplan Zingales index is represented in the OpenAP signal catalog as a composite accounting predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: -1.002* (net income (ni) + depreciation (dp))/total assets (at) + .283*(total assets (at) + market value of equity - book value of equity (ceq) - deferred taxes (txdi))/total assets (at) + 3.319*(debt in current liabilities (dlc) + long-term debt (dltt))/(debt in current liabilities + long-term debt + book value of equity) - 39.368*(Dividends (divamt)/total assets) - 1.315*(cash and short-term investments (che)/total assets). Replace txdi and divamt with 0 if missing. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute KZ for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=KZ; category=composite accounting; data=Accounting; evidence=t=1.1 in conservative port sort. Review the generated entry before using it as a final public corpus item.
