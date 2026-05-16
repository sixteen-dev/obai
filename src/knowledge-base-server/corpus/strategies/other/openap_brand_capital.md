---
entry_type: strategy
id: openap_brand_capital
canonical_name: Brand capital to assets
aliases:
- Brand capital to assets
- BrandCapital
one_line: Cross-sectional equity anomaly that uses Brand capital to assets to rank
  stocks by the signal and form the source-defined long-short spread.
category: other
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
- Not studied in OP. OP studies BrandInvest.
- 'Original-paper replication evidence: not studied for predictability; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Brand capital to assets
  authors:
  - Belo, Lin
  - Vitorino
  year: 2014
  venue: RED
  url: https://www.openassetpricing.com/data/
---
## Thesis
Brand capital to assets is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Brand capital is computed by the perpetual inventory method. In the first year, brand capital is advertising expense divided by (.5 + .1). In subsequent years, we let brand capital depreciate with a rate of .5 and add current advertising expenses. Brand capital is scaled by total assets (at). Set to missing if advertising expense is missing. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute BrandCapital for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BrandCapital; category=investment alt; data=Accounting; evidence=not studied for predictability. Review the generated entry before using it as a final public corpus item.
