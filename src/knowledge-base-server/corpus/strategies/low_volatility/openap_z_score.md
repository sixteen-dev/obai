---
entry_type: strategy
id: openap_z_score
canonical_name: Altman Z-Score
aliases:
- Altman Z-Score
- ZScore
one_line: Cross-sectional equity anomaly that uses Altman Z-Score to long low-signal
  stocks and short high-signal stocks.
category: low_volatility
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
- Intro claims higher bankrupty prob has "significantly" lower returns, but Tab 3
  shows the evidence is mixed for Z Score. Z is only signficant in regs controlling
  for B/M, and has a nonmonotonic realtionship with ret. Unlike for OSCore, there
  is no clear LS strategy. Sign is wrong for nasdaq (Tab3B). One could "fix" this
  signal as we did before by dropping the lowest ZScore quint, as hinted at in Tab
  3A.
- 'Original-paper replication evidence: t=1.59 in univar reg; reported long-short
  return=n/a, t-stat=1.59.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test default risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Altman Z-Score
  authors:
  - Dichev
  year: 1998
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Altman Z-Score is represented in the OpenAP signal catalog as a default risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 1.2*(current assets (act) - current liabilities (lct))/total assets (at) + 1.4*(Retained earnings (re)/total assets (at)) + 3.3*(net income (ni) + interest expense (xint) + total taxes (txt))/total assets (at) + .6*(market value of equity/Total liabilities (lt)) + revenue (revt)/ total assets (at). Include only NYSE stocks. Exclude if SIC code between 4000 and 4999, or above 5999. Exclude if ZScore is in bottom quintile of ZScore (original paper shows non-monotonic returns, as does our replication) The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ZScore for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ZScore; category=default risk; data=Accounting; evidence=t=1.59 in univar reg. Review the generated entry before using it as a final public corpus item.
