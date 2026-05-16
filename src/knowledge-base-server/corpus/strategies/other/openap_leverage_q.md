---
entry_type: strategy
id: openap_leverage_q
canonical_name: Market leverage quarterly
aliases:
- Leverage_q
- Leverageq
- Market leverage quarterly
one_line: Cross-sectional equity anomaly that uses Market leverage quarterly to rank
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test leverage effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Market leverage quarterly
  authors:
  - Bhandari
  year: 1988
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Market leverage quarterly is represented in the OpenAP signal catalog as a leverage predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Total liabilities (lt) divided by market value of equity. The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute Leverage_q for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Leverage_q; category=leverage; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
