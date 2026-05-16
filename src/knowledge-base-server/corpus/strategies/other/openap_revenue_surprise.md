---
entry_type: strategy
id: openap_revenue_surprise
canonical_name: Revenue Surprise
aliases:
- RevSurprise
- Revenue Surprise
- RevenueSurprise
one_line: Cross-sectional equity anomaly that uses Revenue Surprise to long high-signal
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
- No t-stats in many event studies, but many 1% significant results (e.g. table 6)
- 'Original-paper replication evidence: t>2.6 in many event studies; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test sales growth effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Revenue Surprise
  authors:
  - Jegadeesh
  - Livnat
  year: 2006
  venue: JAE
  url: https://www.openassetpricing.com/data/
---
## Thesis
Revenue Surprise is represented in the OpenAP signal catalog as a sales growth predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define revenue per share as quarterly revenue (revtq) divided by quarterly common shares outstanding (cshprq). RevenueSurprise is the 4-quarter change in revenue per share minus the average 4-quarter change in revenue per share over the previous 2 years. RevenueSurprise is scaled by its standard deviation over the previous 2 years. Exclude if price less than 5. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RevenueSurprise for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RevenueSurprise; category=sales growth; data=Accounting; evidence=t>2.6 in many event studies. Review the generated entry before using it as a final public corpus item.
