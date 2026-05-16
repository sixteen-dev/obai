---
entry_type: strategy
id: openap_residual_momentum6m
canonical_name: 6 month residual momentum
aliases:
- 6 month residual momentum
- MomRes6m
- ResidualMomentum6m
one_line: Cross-sectional equity anomaly that uses 6 month residual momentum to rank
  stocks by the signal and form the source-defined long-short spread.
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
- OP only uses 12m version. It turns out that 6m residual momentum is very much like
  the standard 12m version, but I suppose it could have turned out differently.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test momentum effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: 6 month residual momentum
  authors:
  - Blitz, Huij
  - Martens
  year: 2011
  venue: JEmpFin
  url: https://www.openassetpricing.com/data/
---
## Thesis
6 month residual momentum is represented in the OpenAP signal catalog as a momentum predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Run a rolling regression over 36 months of excess return (retrf) on excess market return (mktrf), size and value factors (smb, hml) and compute idiosyncratic returns as the one-month lagged residual. ResidualMomentum is the rolling mean of the residual divided by the rolling standard deviation of the residual, both computed over the past 6 months. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute ResidualMomentum6m for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ResidualMomentum6m; category=momentum; data=Price; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
