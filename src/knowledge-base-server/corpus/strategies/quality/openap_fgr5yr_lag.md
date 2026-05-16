---
entry_type: strategy
id: openap_fgr5yr_lag
canonical_name: Long-term EPS forecast
aliases:
- EPSForeLTlag
- Long-term EPS forecast
- fgr5yrLag
one_line: Cross-sectional equity anomaly that uses Long-term EPS forecast to long
  low-signal stocks and short high-signal stocks.
category: quality
asset_classes:
- equities
typical_holding_period: quarterly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Port sort in Tab 2 is very strong but no t-stats. Regression in Tab 3 E{g} is univariate.
  We find the timing of the lag is important, but it may be more important to compound
  monthly returns to annual, as described on page 1717. If we compound returns, the
  high volatility of the high long-term-growth stocks leads to very poor performance,
  consistent with Table II of OP and Figure 1 of the 2019 JF paper. We are grateful
  to Rafael La Porta for helping us identify this feature. But for our paper, our
  portfolio is just the arithmetic mean of monthly returns for simplicity.
- 'Original-paper replication evidence: t=4.9 in regression; reported long-short return=n/a,
  t-stat=4.9.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test earnings forecast effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Long-term EPS forecast
  authors:
  - La Porta
  year: 1996
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Long-term EPS forecast is represented in the OpenAP signal catalog as a earnings forecast predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Lag long-term earnings forecast (fgr5yr) by 6 months. Then keep only June observations, and fill in missing with most recent obs. Exclude if book equity (ceq), net income (ib), deferred taxes (txdi), dividends (dvp), revenue (sale) or depreciation (dp) is missing. Keep only The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute fgr5yrLag for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=fgr5yrLag; category=earnings forecast; data=Analyst; evidence=t=4.9 in regression. Review the generated entry before using it as a final public corpus item.
