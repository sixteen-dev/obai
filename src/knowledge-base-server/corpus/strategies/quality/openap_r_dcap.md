---
entry_type: strategy
id: openap_r_dcap
canonical_name: R&D capital-to-assets
aliases:
- R&D capital-to-assets
- RDcap
one_line: Cross-sectional equity anomaly that uses R&D capital-to-assets to long high-signal
  stocks and short low-signal stocks.
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
- Table 7 shows that it only works in small firms with a hedge return t-stat of 2.64.
  Works fine EW of course.
- 'Original-paper replication evidence: t=2.6 in long-short; reported long-short return=0.69,
  t-stat=2.64.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test asset composition effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: R&D capital-to-assets
  authors:
  - Li
  year: 2011
  venue: RFS
  url: https://www.openassetpricing.com/data/
---
## Thesis
R&D capital-to-assets is represented in the OpenAP signal catalog as a asset composition predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: R&D capital to assets is the weighted sum of lagged R&D expenditures scaled by the assets (at). We replace xrd with 0 if xrd is missing and compute the numerator as $xrd_t + .8 xrd_{t-1}+ .6xrd_{t-2} + .4 xrd_{t-3} + .2 xrd_{t-4}$. Replace RDcap with missing before 1980 or if firm is in upper two thirds of market cap (shrout*abs(prc)) distribution in a month. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute RDcap for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=RDcap; category=asset composition; data=Accounting; evidence=t=2.6 in long-short. Review the generated entry before using it as a final public corpus item.
