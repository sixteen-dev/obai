---
entry_type: strategy
id: openap_div_yieldst
canonical_name: Predicted div yield next month
aliases:
- DivYieldST
- Predicted div yield next month
one_line: Cross-sectional equity anomaly that uses Predicted div yield next month
  to long high-signal stocks and short low-signal stocks.
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
- HXZ cite Litzenberger and Ramaswamy (LR) for their Dp predictor, but Dp is an annual
  dividend yield that is closer to Keim (1985), which shows very weak predictability.
  LR is actually similar to DivSeason (Hartzmark and Solomon), and we follow LR. LR
  uses a badly behaved regression with 75% of their div yield variable = 0, so we
  are flexible in our approach to mimic their results. Also the paper is old, mostly
  theory, and provides little detail on their data handling. Clear is a judgment call.
- 'Original-paper replication evidence: t=6 in mv reg; reported long-short return=n/a,
  t-stat=6.3.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Predicted div yield next month
  authors:
  - Litzenberger
  - Ramaswamy
  year: 1979
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Predicted div yield next month is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Using CRSP distributions, keep only distcd beginning in 12 and if the third digit of distcd is 3, 4, or 5. Also keep only if stock paid a dividend in the last 12 months. Define Ediv1 = div 2 months ago if distcd's 3rd digit is 0, 1, or 3. Define Ediv1 = div 5 months ago if the distcd 3rd digit is 4. Define Ediv = div 11 months ago if the distcd 3rd digit is 5. Define Edy1 as Ediv1/abs(prc). Finally, discretize Edy1 to smooth around the huge mass at 0 as follows: DivYieldST = 0 if Edy1 = 0, 1 if Edy is between 0 and 0.005, 2 if Edy1 is between 0.005 and 0.010, and 3 if Edy > 0.010. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DivYieldST for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DivYieldST; category=valuation; data=Accounting; evidence=t=6 in mv reg. Review the generated entry before using it as a final public corpus item.
