# Backtest Server Conformance

This document defines what "industry standard" means for the OBaI backtest
server and how the deterministic conformance tests verify it. These tests live
in `tests/test_conformance_*.py` and use committed fixture data from
`tests/conformance_fixtures.py`.

The conformance suite is intentionally separate from the agent regression evals
under `src/obai/evaluation`. Agent evals check routing, tool use, and answer
quality. This suite checks the backtest engine's calculations and execution
behavior directly.

## Research Summary

The relevant public references split into calculation libraries, execution
engines, and financial reasoning benchmarks.

Calculation references worth borrowing:

- TA-Lib is the canonical open-source technical-analysis function library. Its
  docs and Python wrapper describe the indicator families, function API, and
  common OHLCV inputs. OBaI already uses `polars-talib`, a Polars expression
  wrapper for TA-Lib-style functions, so indicator parity tests compare OBaI's
  registry mapping and output naming against direct `polars_talib` expressions.
- empyrical provides Quantopian's common financial risk and performance
  metrics, including cumulative returns, max drawdown, annual volatility,
  Sharpe, Sortino, alpha, and beta. It is also the metric dependency used by
  pyfolio.
- pyfolio is Quantopian's portfolio and risk tear-sheet library and is useful as
  the higher-level convention around empyrical-style return-series analytics.
- QuantStats provides a maintained portfolio analytics package. Its docs are
  useful for the return-period versus trade-level distinction; OBaI explicitly
  asserts that closed-trade stats can differ from QuantStats-style period stats.

Execution references worth borrowing:

- Backtrader documents the standard next-bar market-order model for backtests,
  where a market order submitted by strategy logic fills at the next bar's open.
  It also documents percent slippage applied in the unfavorable direction for
  buy and sell orders.
- Zipline separates slippage and commission models and treats slippage models as
  the component responsible for simulated fill prices and fill volumes. Its
  fixed-spread and volume-share models are useful references for cost-model
  boundaries.
- QuantConnect LEAN documents fill, fee, slippage, and margin as customizable
  "reality models". Its equity fill model documents conservative stop behavior:
  a stop fills at the stop level in a continuous market, but at the opening
  price on unfavorable gaps.
- vectorbt is useful for validating the record-keeping shape of simulated
  orders, fees, prices, sizes, and sides.

Financial reasoning benchmarks checked but not used for this suite:

- FinanceBench, FinQA, TAT-QA, FinEval, and FinBen evaluate financial question
  answering, numerical reasoning, retrieval, or LLM/agent behavior. They are
  relevant to OBaI's agent-level evaluation work, not to deterministic
  backtest-engine conformance.

## Conformance Contract

For this repo, "industry standard" means:

1. Technical indicators match TA-Lib or `polars-talib` function behavior for the
   same OHLCV inputs and parameter mapping. Indicator parity covers the backtest
   engine registry; any narrower agent-facing or tool-facing indicator surface is
   a product exposure choice, not a calculation-conformance mismatch.
2. Return-series metrics use common empyrical/QuantStats-style simple returns,
   sample standard deviation for volatility and Sharpe, full-series downside
   semi-deviation for Sortino, cumulative-equity drawdown, and benchmark
   covariance/variance beta.
3. Trade statistics remain trade-based, not period-based. This is intentional:
   OBaI reports closed-trade win rate, profit factor, average trade return, and
   holding period separately from return-series risk metrics.
4. Signal execution avoids lookahead: entry and signal-exit decisions from
   `close[t]` fill at `open[t+1]`.
5. Intrabar stop and take-profit checks use high/low ranges. If a long stop and
   target are both touched in one bar, the stop wins as the conservative
   assumption.
6. Gap-through stop exits fill at the worse open, matching LEAN-style
   conservative stop behavior.
7. Slippage and spread move fills in the unfavorable direction. Percent
   commission is charged on both entry and exit.
8. Portfolio mode uses shared cash, discrete share counts, exits before entries
   on the same bar, and final close-out of open positions at the last available
   close.
9. Data-quality metadata is observability only. It must count coverage and
   zero/null prices without changing metrics or requiring the agent eval harness.

## Intentional Differences

OBaI intentionally differs from some public libraries in these areas:

- CAGR uses the actual calendar date range in the equity curve. empyrical's
  `annual_return` annualizes by `len(returns) / annualization_factor`. Calendar
  CAGR is easier to explain in API responses and handles irregular date ranges.
- Public metric values are rounded and expressed as percentages where the API
  schema says percentage. empyrical and QuantStats usually return decimals.
- Jensen alpha is arithmetic annualized from per-bar alpha. empyrical's alpha is
  geometrically annualized from mean alpha series. The OBaI value is stable for
  short deterministic fixtures and easier to reconcile from the code.
- Trade-level win rate and profit factor intentionally differ from QuantStats
  period-level win rate and profit factor.
- VWAP is in-house, not TA-Lib parity. OBaI implements session-resetting intraday
  VWAP as cumulative typical-price volume divided by cumulative volume per
  session. Daily VWAP requests are rejected.
- Single-symbol mode tracks proportional equity exposure, while portfolio mode
  tracks discrete shares and cash. Conformance tests cover both models.
- The volume-scaled slippage model is a simplified square-root participation
  model with clamps. It borrows the market-impact concept from Zipline/LEAN but
  is not an exact implementation of either engine's model.

## References

- TA-Lib core library: https://github.com/TA-Lib/ta-lib
- TA-Lib Python documentation: https://ta-lib.github.io/ta-lib-python/
- polars-talib package: https://pypi.org/project/polars-talib/
- empyrical: https://github.com/quantopian/empyrical
- pyfolio: https://quantopian.github.io/pyfolio/
- QuantStats: https://github.com/ranaroussi/quantstats
- Backtrader orders: https://www.backtrader.com/docu/order/
- Backtrader slippage: https://www.backtrader.com/docu/slippage/slippage/
- Zipline slippage source docs: https://zipline.ml4trading.io/_modules/zipline/finance/slippage.html
- QuantConnect LEAN reality modeling: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts
- QuantConnect LEAN equity fill model: https://www.lean.io/docs/v2/lean-engine/class-reference/classQuantConnect_1_1Orders_1_1Fills_1_1EquityFillModel.html
- vectorbt order records: https://vectorbt.dev/api/portfolio/orders/
- FinanceBench: https://github.com/patronus-ai/financebench
- FinQA: https://finqasite.github.io/
- TAT-QA: https://github.com/NExTplusplus/tat-qa
- FinEval: https://huggingface.co/papers/2308.09975
- FinBen: https://arxiv.org/abs/2402.12659
