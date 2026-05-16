---
entry_type: strategy
id: openap_beta_liquidityps
canonical_name: Pastor-Stambaugh liquidity beta
aliases:
- BetaLiquidityPS
- Pastor-Stambaugh liquidity beta
one_line: Cross-sectional equity anomaly that uses Pastor-Stambaugh liquidity beta
  to long high-signal stocks and short low-signal stocks.
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
- OpenAP Price data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=2.54 in VW port sort CAPM alpha; reported
  long-short return=0.533333333, t-stat=2.54.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Price data is available, with a monthly
  rebalance workflow and a desire to test liquidity effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Pastor-Stambaugh liquidity beta
  authors:
  - Pastor
  - Stambaugh
  year: 2003
  venue: JPE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Pastor-Stambaugh liquidity beta is represented in the OpenAP signal catalog as a liquidity predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Monthly excess return (ret -rf) regressed on innovations in liquidity from Pastor's website (\url{https://faculty.chicagobooth.edu/lubos.pastor/research/liq_data_1962_2018.txt}). Use 60 month rolling window regression, and require at least 36 non-missing observations. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BetaLiquidityPS for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BetaLiquidityPS; category=liquidity; data=Price; evidence=t=2.54 in VW port sort CAPM alpha. Review the generated entry before using it as a final public corpus item.
