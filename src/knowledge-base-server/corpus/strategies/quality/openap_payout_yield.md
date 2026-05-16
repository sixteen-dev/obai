---
entry_type: strategy
id: openap_payout_yield
canonical_name: Payout Yield
aliases:
- PayYield
- Payout Yield
- PayoutYield
one_line: Cross-sectional equity anomaly that uses Payout Yield to long high-signal
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
- OP actually uses terciles and NYSE breaks in Tab 6 which shows long-short ports.
  We instead implement ports following Tab 3, because the Tab 6 results appear to
  be sensitive to the details of the lagging. A more rigorous lagging method seems
  to lead to t<2. However, terciles with NYSE breaks seems unnecessarily conservative,
  and Tab 3 replicates quite well, so our implementation focuses on Tab 3, even though
  we write down Tab 6's t-stats.
- 'Original-paper replication evidence: t=3.9 in conservative LS, strong port sort;
  reported long-short return=0.28, t-stat=3.92.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Payout Yield
  authors:
  - Boudoukh et al.
  year: 2007
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Payout Yield is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Sum of dividends (dvc), purchase of common and preferred stock (prstkc) and max(preferred stock redemption value (pstkrv), 0) over market value of equity lagged 6 months. Exclude if PayoutYield $\leq$ 0, financial firm based on SIC code, ceq <= 0, or less than 2 years in CRSP The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute PayoutYield for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=PayoutYield; category=valuation; data=Accounting; evidence=t=3.9 in conservative LS, strong port sort. Review the generated entry before using it as a final public corpus item.
