---
entry_type: strategy
id: openap_beta_dimson
canonical_name: Dimson Beta
aliases:
- BetaDimson
- Dimson Beta
one_line: Cross-sectional equity anomaly that uses Dimson Beta to rank stocks by the
  signal and form the source-defined long-short spread.
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
- Not shown to predict returns. Whole paper is just about forecasting beta,
- 'Original-paper replication evidence: only shown to forecast beta; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test market risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Dimson Beta
  authors:
  - Dimson
  year: 1979
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Dimson Beta is represented in the OpenAP signal catalog as a market risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Rolling regression of daily return (ret - rf) on the same-day, one-day ahead, and one-day lagged value of the market return (mktrf). Rolling regression with 20 observations (minimum 15). BetaDimson is the sum of the three coefficients. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute BetaDimson for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BetaDimson; category=market risk; data=Price; evidence=only shown to forecast beta. Review the generated entry before using it as a final public corpus item.
