---
entry_type: strategy
id: openap_roe
canonical_name: net income / book equity
aliases:
- RoE
- net income / book equity
one_line: Cross-sectional equity anomaly that uses net income / book equity to long
  high-signal stocks and short low-signal stocks.
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
- OP reports mean regression coeff across 90 multiple regressions.
- 'Original-paper replication evidence: t=4.5 in mv reg nonstandard; reported long-short
  return=n/a, t-stat=4.5.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: net income / book equity
  authors:
  - Haugen
  - Baker
  year: 1996
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
net income / book equity is represented in the OpenAP signal catalog as a profitability predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Net income (ni) over book value of equity (ceq). Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RoE for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RoE; category=profitability; data=Accounting; evidence=t=4.5 in mv reg nonstandard. Review the generated entry before using it as a final public corpus item.
