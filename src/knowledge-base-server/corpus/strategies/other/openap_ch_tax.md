---
entry_type: strategy
id: openap_ch_tax
canonical_name: Change in Taxes
aliases:
- ChTax
- Change in Taxes
- TaxGr
one_line: Cross-sectional equity anomaly that uses Change in Taxes to long high-signal
  stocks and short low-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: quarterly
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
- 'Original-paper replication evidence: t = 11.26 in decile sort; reported long-short
  return=1.3, t-stat=11.26.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Change in Taxes
  authors:
  - Thomas
  - Zhang
  year: 2011
  venue: JAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Change in Taxes is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: 4-quarter change in quarterly total taxes (txtq), scaled by lagged total assets (at). The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute ChTax for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=ChTax; category=other; data=Accounting; evidence=t = 11.26 in decile sort. Review the generated entry before using it as a final public corpus item.
