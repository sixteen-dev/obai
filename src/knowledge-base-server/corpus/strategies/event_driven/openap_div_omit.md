---
entry_type: strategy
id: openap_div_omit
canonical_name: Dividend Omission
aliases:
- DivOmit
- Dividend Omission
one_line: Cross-sectional equity anomaly that uses Dividend Omission to long low-signal
  stocks and short high-signal stocks.
category: event_driven
asset_classes:
- equities
typical_holding_period: monthly
engine_fit: reference_only
approximation_notes: Signal requires corporate-event data (earnings announcement
  dates, IPOs, spinoffs, mergers) and event-window logic that the OBaI backtest
  engine does not support natively. Use as routing reference; do not attempt backtest
  execution.
signal_inputs:
- OpenAP Event data
- broad equity universe
- monthly portfolio construction data
known_failure_modes:
- We deviate from OP in not imposing an NYSE/AMEX requirement. This allows our portfolios
  to have a reasonable number of stocks. OP's returns are short DivOmit and long EW
  crsp, which probably pushes up their t-stat. Unlike DivInit, we "hold" for only
  2 month because Table 3 shows that the DivOmit performance is highly concentrated
  early in the event. Very few stocks in the omission portfolio.
- 'Original-paper replication evidence: t=6 in event study; reported long-short return=0.916666667,
  t-stat=6.33.'
- Performance can decay after publication and is sensitive to reporting lags, liquidity
  filters, and transaction costs.
when_to_consider: Broad equity universes where Event data is available, with a monthly
  rebalance workflow and a desire to test payout indicator effects.
when_to_avoid: Avoid narrow universes, stale or restated data, unavailable source
  fields, and implementations that cannot respect source-specific lags or filters.
seminal_papers:
- title: Dividend Omission
  authors:
  - Michaely, Thaler
  - Womack
  year: 1995
  venue: JF
  url: https://www.openassetpricing.com/data/
---
## Thesis
Dividend Omission is represented in the OpenAP signal catalog as a payout indicator predictor. The corpus entry is a source-derived reference for strategy discovery, not a claim that the current backtest engine can reproduce the OpenAP portfolio exactly.

## Signal intuition
The signal definition is: Keep only distcd 2nd digit = 2 or 3. Define firms as quarterly, semi-annual, or annual payers based on payment history. Define a consistent payer as a firm that paid quarterly or semi-annual dividends for the past 18 months, or annual dividends for the past 2 years. An omission is the first month where a consistent payer failed to pay a dividend over the past quarter/6-months/year. Finally, DivOmit = 1 if there is an omission in the past 2 months and 0 otherwise The source direction is to long low-signal stocks and short high-signal stocks.

## Construction sketch
```text
for each rebalance date:
    compute DivOmit for every eligible stock using OpenAP data rules
    rank the eligible universe cross-sectionally
    long low-signal stocks and short high-signal stocks
    rebalance on the source-defined monthly schedule
```

## Notes
Source row: acronym=DivOmit; category=payout indicator; data=Event; evidence=t=6 in event study. Review the generated entry before using it as a final public corpus item.
