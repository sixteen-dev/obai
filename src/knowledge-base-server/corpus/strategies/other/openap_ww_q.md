---
entry_type: strategy
id: openap_ww_q
canonical_name: Whited-Wu Index (Quarterly)
aliases:
- WW_Q
- Whited-Wu index
one_line: Cross-sectional equity anomaly that uses Whited-Wu index to rank stocks
  by the signal and form the source-defined long-short spread.
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
- Insignificant in original paper,
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Whited-Wu index
  authors:
  - Whited
  - Wu
  year: 2006
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Whited-Wu index is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Group data by 3 digit SIC code and month to compute total sales (saleq) by industry. Calculate the Whited-Wu index as -0.091*(quarterly income before extraordninary items (ibq) + quarterly depreciation and amortizarion (dpq)) / (quarterly assets(atq)) - 0.062( if the quarterly dividends per share (dvpsxq) is not missing and greater than 0) + 0.021 * quarterly long-term debt (dlttq) / atq - 0.044 * log(atq) + 0.102 * (four-quarter growth of quarterly Industry Sales) - 0.035 * (one-quarter growth of quarterly sales (saleq)). The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute WW_Q for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=WW_Q; category=external financing; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
