---
entry_type: strategy
id: openap_betarr
canonical_name: Return-market return illiquidity beta
aliases:
- Return-market return illiquidity beta
- betaRR
one_line: Cross-sectional equity anomaly that uses Return-market return illiquidity
  beta to long high-signal stocks and short low-signal stocks.
category: microstructure
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: approximate
approximation_notes: OpenAP signals require dynamic cross-sectional ranking and portfolio
  formation. Current OBaI backtests can only approximate this with a fixed universe,
  screening, or per-symbol proxy rules; do not treat the result as a verbatim OpenAP
  replication.
signal_inputs:
- OpenAP Trading data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Table 4 fits an expected return equation but using a "GMM framework that takes into
  account the pre-estimation of betas," but this seems to be an in-sample fit only.
- 'Original-paper replication evidence: in-sample only; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Trading data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Return-market return illiquidity beta
  authors:
  - Acharya
  - Pedersen
  year: 2005
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Return-market return illiquidity beta is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: see monthly Code The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute betaRR for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=betaRR; category=liquidity; data=Trading; evidence=in-sample only. Review the generated entry before using it as a final public corpus item.
