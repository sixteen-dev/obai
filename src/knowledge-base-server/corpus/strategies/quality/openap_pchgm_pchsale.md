---
entry_type: strategy
id: openap_pchgm_pchsale
canonical_name: Change in gross margin vs sales
aliases:
- ChAssetTurnover
- Change in gross margin vs sales
- pchgm_pchsale
one_line: Cross-sectional equity anomaly that uses Change in gross margin vs sales
  to rank stocks by the signal and form the source-defined long-short spread.
category: quality
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
- GrGMtoGrSales is closer to OP
- 'Original-paper replication evidence: GHZ variant of GrGMToGrSale; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in gross margin vs sales
  authors:
  - Abarbanell
  - Bushee
  year: 1998
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in gross margin vs sales is represented in the OpenAP signal catalog as a profitability alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: growth in (sale-cogs) minus growth in sale The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute pchgm_pchsale for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=pchgm_pchsale; category=profitability alt; data=Accounting; evidence=GHZ variant of GrGMToGrSale. Review the generated entry before using it as a final public corpus item.
