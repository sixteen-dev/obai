---
entry_type: strategy
id: openap_bm
canonical_name: Book to market, original (Stattman 1980)
aliases:
- BM
- Book to market, original (Stattman 1980)
one_line: Cross-sectional equity anomaly that uses Book to market, original (Stattman
  1980) to long high-signal stocks and short low-signal stocks.
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
- OP actually forms portfolios at the end of March based on December FYE data and
  drops stocks with non-Dec FYE. But for consistency with FF1992, we use June as the
  port form month (the irony is noted). OP also drops certain SIC codes, which we
  should implement at the portfolio stage, in time. We previously were citing Rosenberg,
  Reid, Lanstein 1985, but Stattman 1980 should get more credit. RRL does not describe
  its methods in much detail too.
- 'Original-paper replication evidence: risk-adjusted portfolio independence test;
  reported long-short return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Book to market, original (Stattman 1980)
  authors:
  - Stattman
  year: 1980
  venue: Other
  url: https://www.openassetpricing.com/data/
---
## Thesis
Book to market, original (Stattman 1980) is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Log of tangible book equity (ceqt) over market equity matched at FYE The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute BM for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=BM; category=valuation; data=Accounting; evidence=risk-adjusted portfolio independence test. Review the generated entry before using it as a final public corpus item.
