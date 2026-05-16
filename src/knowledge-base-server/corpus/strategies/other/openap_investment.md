---
entry_type: strategy
id: openap_investment
canonical_name: Investment to revenue
aliases:
- InvToRev
- Investment
- Investment to revenue
one_line: Cross-sectional equity anomaly that uses Investment to revenue to long low-signal
  stocks and short high-signal stocks.
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
- OP mean return is only 17 bps per month characteristic adjusted. We deviate somewhat
  from OP in the port sort by going LS 5-1 instead of (4+5) - (1+2) because OP value
  weights within each quintile which we can't do easily in our code.
- 'Original-paper replication evidence: t=2.86 in VW port sort; reported long-short
  return=0.168333333, t-stat=2.86.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test investment effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Investment to revenue
  authors:
  - Titman, Wei
  - Xie
  year: 2004
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Investment to revenue is represented in the OpenAP signal catalog as a investment predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Ratio of capital investment (capx) to revenue (revt) divided by the firm-specific 36-month rolling mean of that ratio. Exclude if revenue less than \$10m. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute Investment for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=Investment; category=investment; data=Accounting; evidence=t=2.86 in VW port sort. Review the generated entry before using it as a final public corpus item.
