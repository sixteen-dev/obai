---
entry_type: strategy
id: openap_beta
canonical_name: CAPM beta
aliases:
- Beta
- CAPM beta
one_line: Cross-sectional equity anomaly that uses CAPM beta to long high-signal stocks
  and short low-signal stocks.
category: low_volatility
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Price data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- '"and then risk-return regressions of (10) are fit month by month" on page 617'
- 'Original-paper replication evidence: t=2.6 univar reg; reported long-short return=n/a,
  t-stat=2.57.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: CAPM beta
  authors:
  - Fama
  - MacBeth
  year: 1973
  venue: JPE
  url: https://www.openassetpricing.com/data/
---
## Thesis
CAPM beta is represented in the OpenAP signal catalog as a risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Coefficient of a 60-month rolling window regression of monthly stock returns minus the riskfree rate on market return minus the risk free rate (ewretd - rf). Exclude if estimate based on less than 20 months of returns. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Beta for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Beta; category=risk; data=Price; evidence=t=2.6 univar reg. Review the generated entry before using it as a final public corpus item.
