---
entry_type: strategy
id: openap_num_earn_increase
canonical_name: Earnings streak length
aliases:
- Earnings streak length
- NumEarnIncrease
one_line: Cross-sectional equity anomaly that uses Earnings streak length to long
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
- This signal isn't exactly found in Loh and Warachka, but table 4 suggests that this
  would predict returns. GHZ have this signal and cite Barth, Elliott and Finn 1999,
  but Barth et al do not study predictability according to Loh and Warachka. I (Andrew)
  couldn't locate Barth et al online during the pandemic, but the abstract of Barth
  et al is consistent with Loh and Warachka's interpretation.
- 'Original-paper replication evidence: similar results in port sorts but not exact;
  reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test earnings growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Earnings streak length
  authors:
  - Loh
  - Warachka
  year: 2012
  venue: MS
  url: https://www.openassetpricing.com/data/
---
## Thesis
Earnings streak length is represented in the OpenAP signal catalog as a earnings growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Number if consecutive 4-quarter increases in ibq, up to 8. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute NumEarnIncrease for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=NumEarnIncrease; category=earnings growth; data=Accounting; evidence=similar results in port sorts but not exact. Review the generated entry before using it as a final public corpus item.
