---
entry_type: strategy
id: openap_asset_liquidity_market
canonical_name: Asset liquidity over market
aliases:
- Asset liquidity over market
- AssetLiquidityMarket
one_line: Cross-sectional equity anomaly that uses Asset liquidity over market to
  rank stocks by the signal and form the source-defined long-short spread.
category: microstructure
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
- Called MWAIL in paper. Not shown to predict returns; Tab 3 for main results, Tab
  6 for ICC of WAIL,
- 'Original-paper replication evidence: no predictability. Correlated with ICC; reported
  long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test asset composition effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Asset liquidity over market
  authors:
  - Ortiz-Molina
  - Phillips
  year: 2014
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Asset liquidity over market is represented in the OpenAP signal catalog as a asset composition predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: (assets (at) + 0.75*(at - short term investments(che)) + 0.5*(at- curren total assets (act) - goodwill(gdwl) - intangibles (itan)) scaled by the one-month lagged market assets (at + end-of-fiscal-year-stock-price (prcc_f) * common shares outstanding (csho) - book equity (ceq)). The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute AssetLiquidityMarket for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AssetLiquidityMarket; category=asset composition; data=Accounting; evidence=no predictability. Correlated with ICC. Review the generated entry before using it as a final public corpus item.
