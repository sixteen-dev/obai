---
entry_type: strategy
id: openap_brand_invest
canonical_name: Brand capital investment
aliases:
- Brand capital investment
- BrandInvest
one_line: Cross-sectional equity anomaly that uses Brand capital investment to long
  low-signal stocks and short high-signal stocks.
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
- OP's stock weighting is VW with a cap on the max weight for a single stock being
  10%. We do EW for simplicity.
- 'Original-paper replication evidence: t=2.0 in port sort; reported long-short return=0.435,
  t-stat=2.01.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Brand capital investment
  authors:
  - Belo, Lin
  - Vitorino
  year: 2014
  venue: RED
  url: https://www.openassetpricing.com/data/
---
## Thesis
Brand capital investment is represented in the OpenAP signal catalog as a investment alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Advertising expenses (xad) divided by BrandCapital. Brand capital is computed by the perpetual inventory method. In the first year, brand capital is advertising expense divided by (.5 + .1). In subsequent years, we let brand capital depreciate with a rate of .5 and add current advertising expenses. Brand capital is scaled by total assets (at). Set to missing if advertising expense is missing. Drop if 1st digit of sic = 4 or 6, and keep only Dec fiscal year ends. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BrandInvest for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BrandInvest; category=investment alt; data=Accounting; evidence=t=2.0 in port sort. Review the generated entry before using it as a final public corpus item.
