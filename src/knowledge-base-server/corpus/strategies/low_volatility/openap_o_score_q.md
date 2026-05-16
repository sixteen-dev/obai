---
entry_type: strategy
id: openap_o_score_q
canonical_name: O Score quarterly
aliases:
- O Score quarterly
- OScore_q
- OScoreq
one_line: Cross-sectional equity anomaly that uses O Score quarterly to rank stocks
  by the signal and form the source-defined long-short spread.
category: low_volatility
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
  a monthly rebalance workflow and a desire to test default risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: O Score quarterly
  authors:
  - Dichev
  year: 1998
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
O Score quarterly is represented in the OpenAP signal catalog as a default risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: OScore = -1.32 - .407*log(at/GNP deflator) + 6.03*(lt/at) - 1.43*( (act - lct)/at) + .076*(lct/act) - 1.72*I(lt > at) - 2.37*(ib/at) - 1.83*(fopt/lt) + .285*(ib + ib$_{t-12}$ + ib$_{t-24}$ < 0) - .521*( (ib - ib$_{t-12}$)/(abs(ib) + .abs(ib$_{t-12}$)) ). fopt = oancf if fopt is missing. Exclude Exclude if SIC code between 3999 and 4999, or greater than 5999. Exclude if price less than 5. Then exclude if OScore is in bottom quintile of OScore (original paper shows non-monotonic returns, as does our replication) The source direction is to rank stocks by the signal and form the source-defined long-short spread.

## Construction sketch
```text
for each rebalance date:
    compute OScore_q for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    rank stocks by the signal and form the source-defined long-short spread
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OScore_q; category=default risk; data=Accounting; evidence=HXZ variant. Review the generated entry before using it as a final public corpus item.
