---
entry_type: strategy
id: openap_delay_non_acct
canonical_name: Non-accounting component of price delay
aliases:
- DelayNonAcct
- Non-accounting component of price delay
one_line: Cross-sectional equity anomaly that uses Non-accounting component of price
  delay to long high-signal stocks and short low-signal stocks.
category: momentum
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
- Tab 7b double sorts on the two delay components and generally finds insignificant
  LS ports. We record the middle values for the first sort. Variable is listed by
  HLZ
- 'Original-paper replication evidence: t=1 in long-short; reported long-short return=0.41,
  t-stat=1.11.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test lead lag effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Non-accounting component of price delay
  authors:
  - Callen, Khan
  - Lu
  year: 2013
  venue: CAR
  url: https://www.openassetpricing.com/data/
---
## Thesis
Non-accounting component of price delay is represented in the OpenAP signal catalog as a lead lag predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Monthly cross-sectional regression of PriceDelay on AccrualQuality, special items (si) scaled by average total assets (at) and earnings surprise (meanest - actual) scaled by its cross-sectional standard deviation. DelayNonAcct is the residual from that regression. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DelayNonAcct for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DelayNonAcct; category=lead lag; data=Accounting; evidence=t=1 in long-short. Review the generated entry before using it as a final public corpus item.
