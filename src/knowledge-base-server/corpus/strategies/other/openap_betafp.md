---
entry_type: strategy
id: openap_betafp
canonical_name: Frazzini-Pedersen Beta
aliases:
- BetaFP
- Frazzini-Pedersen Beta
one_line: Cross-sectional equity anomaly that uses Frazzini-Pedersen Beta to long
  high-signal stocks and short low-signal stocks.
category: other
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
- Though our signal should be close to the original, our portfolios are not. The paper
  shows decile portfolios (table 3) but the factor is actually not constructed from
  going long and short the extreme deciles. Instead, the factor return is based on
  a median split on BetaFP, and then weighting within each portfolio by the rank of
  a stock's BetaFP (higher weight for high rank in above median portfolio, and higher
  weight for low rank in below median portfolio). They also standardize portfolios
  to have a beta of 1.
- 'Original-paper replication evidence: t=7 in nonstandard port sort; reported long-short
  return=0.7, t-stat=7.12.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Frazzini-Pedersen Beta
  authors:
  - Frazzini
  - Pedersen
  year: 2014
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Frazzini-Pedersen Beta is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using daily data, call tempRi the sum of today's return and the past 2 days, call tempRm the sum of today's mkt return and the past 2 days mkt return. Regress return on tempRi and tempRm using past 5 years of data, with a minimum of 3 years. BetaFP is the square root of the r square from this regression times the ratio of the idiosyncratic stock vol and the market vol. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BetaFP for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BetaFP; category=other; data=Price; evidence=t=7 in nonstandard port sort. Review the generated entry before using it as a final public corpus item.
