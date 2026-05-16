---
entry_type: strategy
id: openap_cred_ratdg
canonical_name: Credit Rating Downgrade
aliases:
- CredRatDG
- Credit Rating Downgrade
one_line: Cross-sectional equity anomaly that uses Credit Rating Downgrade to long
  low-signal stocks and short high-signal stocks.
category: other
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires sell-side analyst data (consensus estimates,
  recommendation changes, target prices, IBES-style fields) that the OBaI backtest
  engine does not ingest. Use as routing reference; do not attempt backtest execution.
signal_inputs:
- OpenAP Analyst data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- Most of the returns are earned in first 3 days of announcement. OP uses Moody's
  Default Risk Service data going back to 1970, but our S\&P Credit Ratings data only
  goes back to 1978
- 'Original-paper replication evidence: t=11 in event study w/ special data; reported
  long-short return=1.316666667, t-stat=11.04.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Analyst data is available, with a monthly
  rebalance workflow and a desire to test other effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Credit Rating Downgrade
  authors:
  - Dichev
  - Piotroski
  year: 2001
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Credit Rating Downgrade is represented in the OpenAP signal catalog as a other predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: A downgrade happens if credit rating (splticrm) decreased by at least one notch relative to the previous month. CredRatDG = 1 if a downgrade happened over the past 3 months. OP studies Moody's ratings changes between 1970 and 1997, but our data doesn't begin in earnest until 1986, and our sample definitions apply to returns, and this predictor implicitly averages over the past 6 months. The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute CredRatDG for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=CredRatDG; category=other; data=Analyst; evidence=t=11 in event study w/ special data. Review the generated entry before using it as a final public corpus item.
