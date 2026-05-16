---
entry_type: strategy
id: openap_abnormal_accruals_percent
canonical_name: Percent Abnormal Accruals
aliases:
- AbnAccrPct
- AbnormalAccrualsPercent
- Percent Abnormal Accruals
one_line: Cross-sectional equity anomaly that uses Percent Abnormal Accruals to rank
  stocks by the signal and form the source-defined long-short spread.
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
- "OP says \"Cheng and Thomas (2005) assess 22 different abnomral accruals models\
  \ \u2026 \u2026However, all of their accruals models are scaled by some measure\
  \ of firm size \u2026 never by earnings.\" They don't mention abnormal accruals\
  \ anywhere else."
- 'Original-paper replication evidence: HXZ variant; reported long-short return=n/a,
  t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test accruals effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Percent Abnormal Accruals
  authors:
  - Hafzalla, Lundholm, Van Winkle
  year: 2011
  venue: AR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Percent Abnormal Accruals is represented in the OpenAP signal catalog as a accruals predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: See AbnormalAccruals. signal = AbnormalAccruals x lagged assets / absolute value of ni The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute AbnormalAccrualsPercent for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=AbnormalAccrualsPercent; category=accruals; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
