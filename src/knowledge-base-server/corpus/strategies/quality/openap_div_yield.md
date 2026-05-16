---
entry_type: strategy
id: openap_div_yield
canonical_name: Dividend yield for small stocks
aliases:
- DivYield
- Dividend yield for small stocks
one_line: Cross-sectional equity anomaly that uses Dividend yield for small stocks
  to long high-signal stocks and short low-signal stocks.
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
- Ret is nonmonotic in DivYield, except in small stocks, as seen in Tab 1B. FF3 adjusted
  returns are monotonic however due to size adjustment (Tab 3). Previous papers (Keim
  1985) also find mixed results, this paper is a more careful look. Unclear if likely
  or maybe predictor. Perhaps Keim (1985) or Bloom (1980) or even an earlier paper
  could be cited, but these earlier papers don't show convincing predictability in
  our view. See also Kalay and Michaely (2000)
- 'Original-paper replication evidence: mixed results, small spread; reported long-short
  return=n/a, t-stat=n/a.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Accounting data is available, with
  a monthly rebalance workflow and a desire to test valuation effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Dividend yield for small stocks
  authors:
  - Naranjo, Nimalendran, Ryngaert
  year: 1998
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Dividend yield for small stocks is represented in the OpenAP signal catalog as a valuation predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Define tempDY as 4 times latest dividend (divamt) divided by price (prc). Define positive yield stocks as those which paid a dividend in all of the past 4 quarters. Set DivYield to missing if stock is above the median firm size. This procedure is based on Table 1B. The source direction is to long high-signal stocks and short low-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DivYield for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long high-signal stocks and short low-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DivYield; category=valuation; data=Accounting; evidence=mixed results, small spread. Review the generated entry before using it as a final public corpus item.
