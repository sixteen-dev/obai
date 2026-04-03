# Backtest Server

## Schema ↔ Prompt Dependencies

Changes to the backtest server schema have downstream dependencies in the Strategy Agent prompt (`src/obai/core_agents/prompts/strategy.md`). The prompt teaches the LLM how to construct valid strategy JSON — if the schema changes but the prompt doesn't, the agent will generate invalid or broken strategies.

**When changing any of these, update the strategy prompt to match:**

| Server schema (models/strategy.py) | Prompt section to update |
|---|---|
| `Operand` fields (indicator, constant, time_of_day, time) | "Condition Operands" table + examples |
| `SUPPORTED_OPERATORS` | "Supported Operators" list |
| `SUPPORTED_INDICATORS` / `INDICATOR_REGISTRY` | "Supported Indicators" section + `backtest_get_supported_indicators_tool` output |
| `SUPPORTED_TIMEFRAMES` / `BARS_PER_DAY` | "Intraday Strategy Guidelines" section |
| `PositionSizing` fields / `SUPPORTED_SIZING_METHODS` | "Portfolio Allocation Mode" section + JSON template |
| `RiskManagement` fields | JSON template `risk_management` block |
| `ExecutionConfig` fields | JSON template (if exposed to agent) |
| `SUPPORTED_ALLOCATION_MODES` | "Portfolio Allocation Mode" section |
| New MCP tools added to server.py | "Your Tools" numbered list in prompt |

**Prompt example style:** Concrete shape examples are good, hardcoded market-default examples are not. They teach both schema and strategy priors, and you only want the first. Use neutral placeholders (`<indicator_id>`, `<threshold>`) instead of classic TA defaults (RSI 14/30, SMA 50/200).

**Prompt editing rule:** Do not add inline examples to agent prompts. Examples overfit the model to specific phrasings and crowd out general instruction-following. Tighten gate conditions and word choice instead.

**Other downstream consumers to check:**
- `src/obai/evaluation/test_cases/suite.yaml` — eval queries may reference removed/renamed features
- `src/obai/evaluation/scorers/custom.py` — StrategyContractScorer validates response structure
- `src/obai/core_agents/tests/test_strategy_agent.py` — tests prompt sections and schema fields
- `src/obai/core_agents/tests/test_strategy_eval_contract.py` — tests output contract
- `tests/test_strategy_schema.py` — backtest server schema validation tests
