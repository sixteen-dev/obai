---
entry_type: strategy
id: openap_accrual_quality
canonical_name: Accrual Quality
aliases:
- Accrual Quality
- AccrualQuality
one_line: Cross-sectional equity anomaly that uses Accrual Quality to rank stocks
  by the signal and form the source-defined long-short spread.
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
- Table 2 studies correlation between cost of debt proxies (E/P, beta) and accrual
  quality. Table 3 regresses stock returns on contemporaneous accrual LS port returns.
- 'Original-paper replication evidence: correlated with E/P and factor structure;
  reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Accrual Quality
  authors:
  - Francis, LaFond, Olsson, Schipper
  year: 2005
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Accrual Quality is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define Accruals (tempAccruals) as (difference between current assets (act) and one-year lagged current assets) - (difference between cash and short-term investments (che) and one-year lagged cash and short-term investments) - (difference between current liabilities (lct) and one-year lagged current liabilities) + (difference between debt in current liabilities (dlc) and one-year lagged debt in current liabilities) - (depreciation and amortization (dp)) all divided by (mean of total assets (at) and one-year lagged total assets ). Create tempCAcc as tempAccruals + dp/( (at + l.at)/2), tempRev as sale/( (at + l.at)/2), tempDelRev as tempRev - l.tempRev, tempPPE as ppegt/( (at + l.at)/2) where ppegt is total gross property, plant and equipment and tempCFO as ib/( (at + l.at)/2) - tempAccruals where ib is the income before extraordinary items. Run a regression for each year and industry of tempCAcc on the current value and one year lead and lag of tempDelRev and tempPPE. Save the regression residuals and replace with missing if there are not at least 20 obersvations per year and industry. Calculate accrual quality (AQ) as the standard deviation of residuals over 4 years. If more than one observation is missing set AQ to missing. Replace AccrualQuality by the one-year lagged AQ to make sure the signal is available at time of investment. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute AccrualQuality for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AccrualQuality; category=accruals; data=Accounting; evidence=correlated with E/P and factor structure. Review the generated entry before using it as a final public corpus item.
