---
entry_type: strategy
id: openap_residual_momentum
canonical_name: Momentum based on FF3 residuals
aliases:
- MomResid
- Momentum based on FF3 residuals
- ResidualMomentum
one_line: Cross-sectional equity anomaly that uses Momentum based on FF3 residuals
  to long high-signal stocks and short low-signal stocks.
category: momentum
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
- 1M in the table refers to the holding period, not the signal measurement period.
- 'Original-paper replication evidence: t=8 in long-short ff3+ alpha; reported long-short
  return=0.933333333, t-stat=8.218550486.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Momentum based on FF3 residuals
  authors:
  - Blitz, Huij
  - Martens
  year: 2011
  venue: JEmpFin
  url: https://www.openassetpricing.com/data/
---
## Thesis
Momentum based on FF3 residuals is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Run a rolling regression over 36 months of excess return (retrf) on excess market return (mktrf), size and value factors (smb, hml) and compute idiosyncratic returns as the one-month lagged residual. ResidualMomentum is the rolling mean of the residual divided by the rolling standard deviation of the residual, both computed over the past 11 months. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ResidualMomentum for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ResidualMomentum; category=momentum; data=Price; evidence=t=8 in long-short ff3+ alpha. Review the generated entry before using it as a final public corpus item.
