---
entry_type: strategy
id: openap_ww
canonical_name: Whited-Wu Index
aliases:
- WW
- Whited-Wu index
one_line: Cross-sectional equity anomaly that uses Whited-Wu index to long high-signal
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
- OP shows ff3-style long-short portfolio with value-weighting first, but we focus
  on equal-weighting for simplicity. Regardless the results are similar in OP, and
  OP states "financially constrained firms earn a positive, albeit statistically insignificant
  risk premium."
- 'Original-paper replication evidence: t=1.3 in port sort; reported long-short return=0.23,
  t-stat=1.32.'
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
The signal definition is: Group data by 3 digit SIC code and month to compute total sales (sale) by industry. Calculate the Whited-Wu index as -0.091*(income before extraordninary items (ib) + depreciation and amortizarion (dp)) / (4 * assets(at)) - 0.062( if the dividends per share (dvpsx_c) is not missing and greater than 0) + 0.021 * long-term debt (dltt) / at - 0.044 * log(at) + 0.102 * (twelve-month growth of Industry Sales) / 4 - 0.035 * (one-month growth of sales) / 4. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute WW for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=WW; category=external financing; data=Accounting; evidence=t=1.3 in port sort. Review the generated entry before using it as a final public corpus item.
