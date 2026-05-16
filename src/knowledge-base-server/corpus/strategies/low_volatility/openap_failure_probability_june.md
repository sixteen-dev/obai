---
entry_type: strategy
id: openap_failure_probability_june
canonical_name: Failure Probability (June Variant)
aliases:
- Failure probability
- FailurePrJune
- FailureProbabilityJune
one_line: Cross-sectional equity anomaly that uses Failure probability to rank stocks
  by the signal and form the source-defined long-short spread.
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
- Tab6 Mean LS 2080 t-stat 1.41. We previously used monthly holding periods and got
  large t-stats.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test default risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Failure probability
  authors:
  - Campbell, Hilscher
  - Szilagyi
  year: 2008
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Failure probability is represented in the OpenAP signal catalog as a default risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Failure probability is -9.16 -.058*PRICE + .075*MB - 2.13*CASHMTA -.045*RSIZE + 1.41*IdioRisk - 7.13*EXRETAVG + 1.42*TLMTA - 20.26*NIMTAAVG. PRICE is log(min(abs(prc), 15)); MB is shrout*abs(prc)/ceqq; CASHMTA is cheq/(shrout*abs(prc) + ltq); RSIZE is log(shrout*abs(prc)/ sum of shrout*abs(prc) for the largest 500 companies each month); IdioRisk is defined above, EXRETAVG is the weighted average excess return (log(1 + ret) - log(1 + mktrf)) over the previous 12 months, with weight on month t-j being $\phi^j$ and the sum scaled by $\frac{1-\phi}{1-\phi^{12}}$; TLMTA is total liabilities (ltq/(shrout*abs(prc)); NIMTAAVG is a weighted average of net income over total assets (ibq/(shrout*abs(prc) + ltq)) over four quarters, with weight $\phi^q$ on quarter $t-q$ and the sum scaled by $\frac{1-\phi^3}{1-\phi^{12}}$. $\phi = 2^{-\frac{1}{3}}$. All input variables are winsorized at the 5th and 95th percentile. Exclude if price less than 1. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute FailureProbabilityJune for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=FailureProbabilityJune; category=default risk; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
