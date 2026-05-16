---
entry_type: strategy
id: openap_o_score
canonical_name: O Score
aliases:
- O Score
- OScore
one_line: Cross-sectional equity anomaly that uses O Score to long low-signal stocks
  and short high-signal stocks.
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
- Table 4 has returns by portfolio, but no t-stats. Table 5 shows t=3.36 if you long
  low 70% and short high 10%. OP does not mention price screen, but without the screen
  results are far, and with it results are very close.
- 'Original-paper replication evidence: t=3.36 in LS port; reported long-short return=1.17,
  t-stat=3.36.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test default risk effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: O Score
  authors:
  - Dichev
  year: 1998
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
O Score is represented in the OpenAP signal catalog as a default risk predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: OScore = -1.32 - .407*log(at/GNP deflator) + 6.03*(lt/at) - 1.43*( (act - lct)/at) + .076*(lct/act) - 1.72*I(lt > at) - 2.37*(ib/at) - 1.83*(fopt/lt) + .285*(ib + ib$_{t-12}$ + ib$_{t-24}$ < 0) - .521*( (ib - ib$_{t-12}$)/(abs(ib) + .abs(ib$_{t-12}$)) ). fopt = oancf if fopt is missing. Exclude Exclude if SIC code between 3999 and 4999, or greater than 5999. Exclude if price less than 5. Then exclude if OScore is in bottom quintile of OScore (original paper shows non-monotonic returns, as does our replication) The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute OScore for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=OScore; category=default risk; data=Accounting; evidence=t=3.36 in LS port. Review the generated entry before using it as a final public corpus item.
