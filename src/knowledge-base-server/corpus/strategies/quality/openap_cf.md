---
entry_type: strategy
id: openap_cf
canonical_name: Cash flow to market
aliases:
- CF
- CF2Price
- Cash flow to market
one_line: Cross-sectional equity anomaly that uses Cash flow to market to long high-signal
  stocks and short low-signal stocks.
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=3.4 in port sort; reported long-short return=0.658333333,
  t-stat=3.379.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Cash flow to market
  authors:
  - Lakonishok, Shleifer, Vishny
  year: 1994
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Cash flow to market is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Net income (ib) plus depreciation (dp) divided by market equity. Exclude NASDAQ stocks. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CF for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CF; category=valuation; data=Accounting; evidence=t=3.4 in port sort. Review the generated entry before using it as a final public corpus item.
