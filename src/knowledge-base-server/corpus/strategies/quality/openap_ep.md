---
entry_type: strategy
id: openap_ep
canonical_name: Earnings-to-Price Ratio
aliases:
- EP
- Earnings-to-Price Ratio
one_line: Cross-sectional equity anomaly that uses Earnings-to-Price Ratio to long
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
- MP use sample dates 1964-1971, but the original paper uses 1956-1971. Also, original
  uses Dec 31 mve_c, so we lag by 6 months our monthly mve_c to approximate.
- 'Original-paper replication evidence: monotonic port sort but no LS; reported long-short
  return=0.58, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings-to-Price Ratio
  authors:
  - Basu
  year: 1977
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings-to-Price Ratio is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: ib / lag(market value of equity, 6 months). NYSE stocks only. Exclude if EP < 0. Lag simulates the Dec 31 market equity used in original paper The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EP for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EP; category=valuation; data=Accounting; evidence=monotonic port sort but no LS. Review the generated entry before using it as a final public corpus item.
