---
entry_type: strategy
id: openap_debt_issuance
canonical_name: Debt Issuance
aliases:
- Debt Issuance
- DebtIssuance
one_line: Cross-sectional equity anomaly that uses Debt Issuance to long low-signal
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
- OP uses "Investment Dealers' Digest Directory of Corporate Financing" but we use
  Compustat.
- 'Original-paper replication evidence: t = 2.19 FF3 alpha on long port; reported
  long-short return=0.29, t-stat=2.19.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test external financing effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Debt Issuance
  authors:
  - Spiess
  - Affleck-Graves
  year: 1999
  venue: JFE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Debt Issuance is represented in the OpenAP signal catalog as a external financing predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Equal to 1 if debt issuance (dltis) greater 0 and 0 otherwise. Exclude if share code > 11 or missing book-to-market. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DebtIssuance for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DebtIssuance; category=external financing; data=Accounting; evidence=t = 2.19 FF3 alpha on long port. Review the generated entry before using it as a final public corpus item.
