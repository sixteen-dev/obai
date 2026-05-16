---
entry_type: strategy
id: openap_tax
canonical_name: Taxable income to income
aliases:
- Tax
- Tax2E
- Taxable income to income
one_line: Cross-sectional equity anomaly that uses Taxable income to income to long
  high-signal stocks and short low-signal stocks.
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
- Table 5 shows regressions, but it only works in the subsample 1973-1992. Then again
  Table 6 shows 1993-2000 works as long as you drop 1998. Focuses on forecasting earnings,
  like other accounting papers.
- 'Original-paper replication evidence: t=3.9 in regression; reported long-short return=n/a,
  t-stat=3.851.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Taxable income to income
  authors:
  - Lev
  - Nissim
  year: 2004
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Taxable income to income is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Ratio of Taxes paid and tax share of net income. Numerator is defined as the sum of foreign (txfo) and federal (txfed) income taxes. If either one is missing, numerator is defined as total taxes (txt) minus deferred taxes (txdi). Denominator is the product of the prevailing tax rate and net income (ib). Tax rate is .48 before 1979, .46 from 1979 to 1986, .4 in 1987, .34 between 1988 and 1992 and .35 from 1993 onwards. If net income is negative, and the numerator is positive, tax is defined as 1. Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Tax for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Tax; category=other; data=Accounting; evidence=t=3.9 in regression. Review the generated entry before using it as a final public corpus item.
