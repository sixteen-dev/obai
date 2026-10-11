# Prompt for OpenClaw or another host agent

Copy the block below. Supply the complete skills bundle and credentials via
the host's secret store/environment. This authorizes setup and eligible
Alpaca **paper** automation; it does not authorize live trading.

```text
Set up persistent OBaI research and Alpaca paper-trading jobs using the supplied skills and direct MCP servers. Use your own model as the hub; do not install or invoke the OBaI CLI/runtime.

You may configure local connections, install standalone dependencies, create persistent jobs, and implement/test the minimal execution adapter needed. Once the checks below pass, enable eligible paper orders within my saved scope without asking approval for each routine order. Finish independent setup if a prerequisite is missing, then report the specific blocker. Never switch to live trading.

1. Read and install

Locate the checkout or supplied bundle. Read obai-hub/SKILL.md and setup.md, load the matching specialist skills as needed, and read autotrader/SKILL.md, context.md and signal-input.md. Preserve complete skill directories and their support files. Exclude the obai CLI-wrapper skill, local virtualenvs, secrets and another user's memory. Use absolute installed paths in jobs and install AutoTrader from its locked standalone project, including its dev group on any runner that must run its offline tests.

Reuse my saved universe, strategy objective and paper capital cap. Ask for missing material inputs together; do not invent them or treat the example $100,000 memory file as capital. Continue connection setup and non-trading jobs while those inputs are pending. Default scope is long-only US stocks/ETFs and completed daily bars, no shorts or margin. Keep any stricter limits already saved.

2. Connect and verify

Inspect the installed host's MCP and scheduler help. For OpenClaw, use mcp.servers with Streamable HTTP; the bundled mcp-config.json is Claude-style, not a replacement OpenClaw config. Preserve existing connections. Reuse healthy servers or start the required Compose services; resolve URLs reachable from the actual scheduled runtime. Probe required servers and list their tools there. Optional unavailable domains should not block unrelated jobs.

Read ALPACA_API_KEY and ALPACA_SECRET_KEY from the secret store/environment. Verify the paper account, clock, positions and orders read-only. Never print keys or embed them in prompts, logs or commits. Report missing variable names through the host's secure setup mechanism, not a request to paste secrets into chat. Market-data provider keys belong in the MCP services as described in setup.md. No OBaI OpenAI key is needed for ordinary direct-MCP analysis.

3. Freeze an executable strategy

Use obai-strategy to test the saved universe/objective, with realistic costs and an out-of-sample assessment. Preserve the exact JSON, version, job IDs, data/execution assumptions, warnings and verdict in durable task storage. Resume asynchronous jobs by ID and deliver the completed artifact. Pending, rejected and needs_more_research candidates cannot place orders. No eligible candidate is a valid outcome.

The supplied signal helper evaluates a narrow set of daily predicates; it is not a full live strategy engine. Verify every selected indicator/source, previous/current bar, warm-up, operator, adjustment basis, sizing rule, stop/target, trailing rule, holding limit, cooldown and position-state transition against backtest fixtures. Build only the missing deterministic adapter needed by the selected candidate, or leave it in research mode. Do not silently simplify rules. Treat a 09:35 submission versus a modeled next-open fill as an explicit execution deviation and reject the handoff if it invalidates the strategy. Periodic monitoring does not reproduce intrabar/broker-held stops.

Manage only positions assigned to this bot's saved strategies. Keep pre-existing holdings unmanaged unless I included them. Research may propose a new version but cannot modify active exits or add discretionary overrides.

4. Configure execution state and limits

Use the bundled submit/close helpers with paper=True, stable client_order_id values and one shared AUTOTRADER_STATE_DIR on one host for every execution job on the account. Derive each ID from account, strategy version, symbol, signal bar and action; persist the exact request. Reconcile repeated or uncertain submissions by the same ID. Never delete an unresolved intent or generate a new ID to force a retry. Disable entries while account/order state is uncertain.

Apply my stricter limits or defaults: MAX_POSITION_PCT=10, MAX_DAILY_TRADES=20, MAX_DAILY_LOSS_PCT=3, MAX_EXPOSURE_PCT=90, MAX_POSITIONS=10. The execution adapter must additionally enforce my explicit paper capital cap and long-only ownership scope. Use actual equity and available cash, accounting for outstanding orders. Reject invalid or stale required inputs. The scripts enforce pending-order checks and conservative reservations; they do not enforce every strategy-specific rule or my capital cap by themselves.

Evaluate exits before entries. Keep reconciliation and permitted protective exits running after entry circuit breakers or an earlier trade. Reconcile existing protective orders before another sell. Only confirmed fills change holdings or realized P&L; accepted/partial/canceled/rejected states remain distinct. Give pending entry orders a bounded lifetime and cancel/reconcile them through the same account lock according to the saved plan. Qualitative entry vetoes must be predefined; unavailable mandatory checks block entries.

Provide an entries kill switch and a separate all-automation pause. A pause must not silently cancel protection or liquidate unmanaged holdings. Keep dry_run enabled until the validation and eligible-plan gates pass, then persist the authorized enabled state so jobs do not revert to the skill's default.

5. Create real persistent jobs

Inspect existing jobs and update matching names rather than duplicating them. Each job must load the saved plan/state from absolute paths; it cannot depend on this conversation. Use America/New_York explicitly for every schedule:
- obai-preflight: 09:15 weekdays; probe required services, reconcile, prepare catalysts/completed-bar inputs; no orders.
- obai-paper-trade: 09:35 weekdays; evaluate the frozen plan, exits before entries, submit eligible paper intents and reconcile.
- obai-risk-monitor: every five minutes during weekday session hours; prefer a deterministic command. Reconcile and manage configured protection/exits; no fresh discretionary entries.
- obai-reconcile: 16:20 weekdays; reconcile fills, pending orders, cash/positions and journal; no entries.
- obai-weekly-review: 17:00 Friday; review paper results, costs and drift; no orders or silent strategy replacement.

Use Alpaca's actual clock/calendar for holidays, early closes and session boundaries; weekday cron alone is insufficient. Derive required closing actions from actual next_close and the plan. Bound run duration, serialize mutations and persist results. Report locally or in our existing private session; do not add recipients/webhooks without my instruction.

6. Verify and enable

Run AutoTrader's offline tests plus focused adapter tests for duplicate triggers, crash/timeout recovery, partial fills, pending exposure, position/capital limits, completed-bar/crossover signals, closed/early-close sessions and exits after entry limits. Run one isolated scheduled dry run with submissions disabled and verify secrets, MCP tools, paths, locks and durable state in that runtime. Do not place a dummy order for connectivity.

Enable paper execution only when credentials, a saved capital cap, an eligible frozen strategy, the full adapter and the required checks are ready. Otherwise retain useful non-trading jobs, leave order placement disabled and state the exact remaining blocker. Do not claim host/CLI quality parity without representative comparisons using the same tool evidence.

Finish with installed paths; MCP probe results; credential readiness without values; strategy/adapter eligibility; test and isolated dry-run evidence; job IDs, schedules, timezone, enabled state and next runs; state/journal locations; exact pause/resume instructions. Distinguish configured, dry-run verified, paper enabled and blocked states. Complete the setup rather than returning only a plan.
```
