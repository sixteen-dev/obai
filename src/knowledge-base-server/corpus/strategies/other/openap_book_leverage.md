---
entry_type: strategy
id: openap_book_leverage
canonical_name: Book leverage (annual)
aliases:
- Book leverage (annual)
- BookLev
- BookLeverage
one_line: Cross-sectional equity anomaly that uses Book leverage (annual) to long
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
- OpenAP notes should be reviewed before production use.
- 'Original-paper replication evidence: t=5.3 in mv reg; reported long-short return=n/a,
  t-stat=5.34.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test leverage effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Book leverage (annual)
  authors:
  - Fama
  - French
  year: 1992
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Book leverage (annual) is represented in the OpenAP signal catalog as a leverage predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Total assets (at) divided by book value of equity plus deferred taxes (txditc) and preferred stock. Equity is shareholder equity (seq) if available, or book equity (ceq) plus preferred stock (pstk, if missing pstkrv, if missing pstkl), or total assets minus total liabilities (lt). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BookLeverage for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BookLeverage; category=leverage; data=Accounting; evidence=t=5.3 in mv reg. Review the generated entry before using it as a final public corpus item.
