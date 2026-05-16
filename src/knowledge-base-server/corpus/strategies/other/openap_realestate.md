---
entry_type: strategy
id: openap_realestate
canonical_name: Real estate holdings
aliases:
- Real estate holdings
- RealEstate
- realestate
one_line: Cross-sectional equity anomaly that uses Real estate holdings to long high-signal
  stocks and short low-signal stocks.
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
- OP finds smaller t-stat = 1.3 EW. OP is mainly theory. Does not describe data handling
  super closely
- 'Original-paper replication evidence: t=1.8 (VW) and t= 1.28 (EW) in port sort;
  reported long-short return=0.244166667, t-stat=1.8.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test asset composition effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Real estate holdings
  authors:
  - Tuzel
  year: 2010
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Real estate holdings is represented in the OpenAP signal catalog as a asset composition predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Industry-adjusted value of real estate holdings. Real estate holdings are (fatb+fatl)/ppegt if available, and (ppenb+ppenl)/ppent otherwise. Drop firms in 2 digit sics with < 5 firm or missing at or missing both ppent and ppegt. Subtract monthly industry-mean at the 2 digit SIC level. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute realestate for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=realestate; category=asset composition; data=Accounting; evidence=t=1.8 (VW) and t= 1.28 (EW) in port sort. Review the generated entry before using it as a final public corpus item.
