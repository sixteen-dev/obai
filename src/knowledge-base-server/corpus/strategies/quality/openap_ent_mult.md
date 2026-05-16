---
entry_type: strategy
id: openap_ent_mult
canonical_name: Enterprise Multiple
aliases:
- EntMult
- Enterprise Multiple
one_line: Cross-sectional equity anomaly that uses Enterprise Multiple to long low-signal
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
- Table 3 Panel B. Table 3A shows raw returns but no t-stats.
- 'Original-paper replication evidence: t=6.54 in decile sort CAPM alpha; reported
  long-short return=0.95, t-stat=6.54.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Enterprise Multiple
  authors:
  - Loughran
  - Wellman
  year: 2011
  venue: JFQA
  url: https://www.openassetpricing.com/data/
---
## Thesis
Enterprise Multiple is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Market value of equity + long-term debt (dltt) + debt in current liabilities (dlc) + deferred charges (dc) - cash and short-term investments (che) , divided by operating income (oibdp). Exclude if missing book equity or negative operating income. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute EntMult for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=EntMult; category=valuation; data=Accounting; evidence=t=6.54 in decile sort CAPM alpha. Review the generated entry before using it as a final public corpus item.
