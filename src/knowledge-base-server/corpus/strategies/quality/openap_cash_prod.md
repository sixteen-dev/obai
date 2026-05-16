---
entry_type: strategy
id: openap_cash_prod
canonical_name: Cash Productivity
aliases:
- Cash Productivity
- CashProd
one_line: Cross-sectional equity anomaly that uses Cash Productivity to long low-signal
  stocks and short high-signal stocks.
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
- Stats are from WP version.
- 'Original-paper replication evidence: t=3.6 in regression; reported long-short return=n/a,
  t-stat=3.6.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test profitability alt effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Cash Productivity
  authors:
  - Chandrashekar
  - Rao
  year: 2009
  venue: WP
  url: https://www.openassetpricing.com/data/
---
## Thesis
Cash Productivity is represented in the OpenAP signal catalog as a profitability alt predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Calculate market value of equity (mve_c) as absolute price (prc) times number of shares outstanding (shrout). Cash productivity is equal to the difference between mve_c and total assets (at) divided by cash and short-term investments (che). The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CashProd for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CashProd; category=profitability alt; data=Accounting; evidence=t=3.6 in regression. Review the generated entry before using it as a final public corpus item.
