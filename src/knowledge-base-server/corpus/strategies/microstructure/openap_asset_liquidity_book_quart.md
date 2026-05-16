---
entry_type: strategy
id: openap_asset_liquidity_book_quart
canonical_name: Asset liquidity over book (qtrly)
aliases:
- Asset liquidity over book (qtrly)
- AssetLiquidityBookQuart
one_line: Cross-sectional equity anomaly that uses Asset liquidity over book (qtrly)
  to rank stocks by the signal and form the source-defined long-short spread.
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
- Not shown to predict returns; Tab 3 for main results, Tab 6 for ICC of WAIL,
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test asset composition effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Asset liquidity over book (qtrly)
  authors:
  - Ortiz-Molina
  - Phillips
  year: 2014
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Asset liquidity over book (qtrly) is represented in the OpenAP signal catalog as a asset composition predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: (short term investments (cheq) + 0.75*(actq - short term investments(cheq)) + 0.5*(atq- curren total assets (actq) - goodwill(gdwlq) - intangibles (itanq)) scaled by the one-month lagged book assets (atq). Replace goodwill and intangibles with 0 if they are missing. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute AssetLiquidityBookQuart for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AssetLiquidityBookQuart; category=asset composition; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
