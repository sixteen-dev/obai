---
name: obai-e2e-regression
description: Run the full OBaI end-to-end regression suite — drives the obai CLI across the curated case set, resolves each Opik trace, judges pass/fail. Use ONLY when the user wants the WHOLE suite run after a substantive change. Triggers on "run e2e regression", "/obai-e2e-regression", "validate obai after this change", "regression test obai", "run the full obai suite", "make sure nothing broke before I merge", "run all the e2e tests". Do NOT use for single-query debugging, inspecting one trace ID (that's opik-trace-inspect), running just one specialist, or unit-test scope. Do NOT run automatically after detecting code changes — wait for the user to ask.
---

# OBaI E2E Regression

Black-box regression harness. Drives the real `obai` CLI, resolves the matching Opik trace per run, applies a deterministic rubric. You are the judge — the bundled scripts only do mechanical work.

## When to use

Trigger only on explicit user instruction. Never run proactively. Typical phrasing: "run e2e regression", "validate obai after my change", "/obai-e2e-regression", "regression test the hub prompt update".

## Pre-flight

Run once before any cases:

```bash
uv run python .claude/skills/obai-e2e-regression/scripts/preflight.py
```

Exits 0 ready, non-zero with reason. Do not proceed if it fails — surface the message verbatim and ask the user to fix it.

## Run layout

Pick a run directory once: `runs/$(date +%Y-%m-%d)/$(date +%H%M%S)/`. Create it. All per-case JSON checkpoints and the report go inside.

```bash
RUN_DIR=".claude/skills/obai-e2e-regression/runs/$(date -u +%Y-%m-%d)/$(date -u +%H%M%S)"
mkdir -p "$RUN_DIR"
```

## Per-case loop

Drive cases serially. Default set: every case in `cases/cases.yaml` whose `disabled` flag is not true. The user may narrow the set with phrases like "smoke only", "options cases", "just MD1 and F1" — these are **filters Claude applies before looping**, not flags on `run_one.py`. The script only takes `--id`. Map filters as: "smoke" → `smoke: true`, "<feature>" → `feature` field, "<ID list>" → `id` field.

**Disabled cases.** Cases with `disabled: true` are skipped by default. Surface them in the report as a single `disabled` summary line ("skipped N disabled cases: <ids> — <reasons>") so the user knows what didn't run. Run a disabled case only when the user explicitly names it.

**Report header.** Before the first case in this run, write the header to `$RUN_DIR/report.md` (table heading + columns). On a resumed run the file already exists — do NOT rewrite the header, just append rows.

For each case ID:

1. **Skip if already judged.** If `$RUN_DIR/report.md` already contains a row matching `| <ID> |`, skip — the case was judged in an earlier slice of this run. Otherwise read `$RUN_DIR/<id>.json` if it exists; if not, run the helper.
2. **Run** the helper (only if no checkpoint exists yet):
   ```bash
   uv run python .claude/skills/obai-e2e-regression/scripts/run_one.py \
     --id <CASE_ID> --run-dir "$RUN_DIR"
   ```
   The helper writes `<id>.json` atomically and echoes it to stdout. If `<id>.json` already exists it just dumps the cached content and exits 0.
3. **Judge** using the rubric below — work from the JSON packet, especially `trace.curated`.
4. **Append** one row to `$RUN_DIR/report.md`. Write a fail/needs-review block beneath it when applicable.

If the helper exits non-zero on infrastructure (Opik down, CLI hang), mark the case `inconclusive`, log the reason, and continue.

## Validation rubric

Three layers. Stop and report **FAIL** the moment any hard rule fails — don't keep checking that case.

### Layer 1 — Trace flow (routing & skill loading)

Source: the `trace.curated` text in the JSON packet. It already has named sections (`SKILL LOADS:`, `STRATEGY_ANALYSIS CALLS`, etc.) — match against those, don't try to re-query Opik.

- **Skill loads.** Every name in `expected_skills` must appear under the `SKILL LOADS:` section. A name in `expected_skills_absent` must NOT appear there. Missing → FAIL `routing.skill_missing`. Present-when-banned → FAIL `routing.skill_overload`.
- **Specialist coverage.** Every name in `expected_tools` must appear as a tool span on the hub (named exactly: `market_data_analysis`, `fundamentals_analysis`, etc.). Missing → FAIL `routing.specialist_missing`.
- **Sequence (partial order).** When `expected_sequence` is set, for each adjacent pair `(A, B)` in that list, the first start-time of `A` must precede the first start-time of `B`. Tools not listed in `expected_sequence` may interleave anywhere. Out-of-order → FAIL `routing.sequence_violation`.
- **No over-routing.** Specialists called outside `expected_tools ∪ allowed_extras` → soft fail `routing.unexpected_specialist` → NEEDS_REVIEW.
- **Threshold fidelity** (strategy cases). Inside the `Hub-to-strategy input` block of the curated trace, every numeric threshold and indicator-period syntax from the user query must survive verbatim ("30", "70", "RSI(14)", "9:45", "1.5%"). Operator phrases may be paraphrased ("drops below" ↔ "<", that's expected). Numeric paraphrase → FAIL `routing.hub_paraphrase`.
- **No error spans on happy path.** Any span with `error_info` set when `expect_rejection: false` → FAIL `trace.error_span:<span_name>`.

### Layer 2 — Specialist output

Apply the rejection rule first; gate the rest on `expect_rejection: false`.

- **Rejection-path cleanliness** (`expect_rejection: true`). Guardrail span must fire AND no specialist tool spans should appear. Specialist called → FAIL `guardrail.leaky`. (When this rule applies, skip the remaining Layer 2 checks.)
- **Non-empty payload.** Each specialist's output in the curated trace must be > 0 chars and not a bare error envelope. Empty → FAIL `specialist.<name>.empty`.
- **Tool error envelope.** If any specialist's response payload contains `isError: true` or an MCP error block on the happy path → FAIL `specialist.<name>.tool_error`. (This catches MCP-layer failures the hub silently rendered around.)
- **Strategy contract** (strategy cases). The curated trace's strategy block must contain `Verdict`, operator list, and a `Total trades` line. Missing any → FAIL `specialist.strategy.contract_broken`. Total trades = 0 → FAIL `specialist.strategy.zero_trades`.
- **Async job stub** (`expect_async_job: true`). The strategy is expected to return a job-id stub, then deliver the full contract on a second turn the runner drives automatically.
  - **First turn:** hub final output must contain `Job ID:` + a numeric ETA. Missing → FAIL `specialist.strategy.async_stub_broken`.
  - **Followup presence:** `packet.followup` must be non-null. Null → FAIL `specialist.strategy.async_followup_missing` (runner couldn't parse a job id, or the follow-up CLI run failed).
  - **Followup contract:** `packet.followup.trace.curated` must contain `Verdict`, operator list, and a `Total trades` line. Missing any → FAIL `specialist.strategy.contract_broken`. Total trades = 0 → FAIL `specialist.strategy.zero_trades`.
  - When this rule applies, replace the standard Strategy-contract check above with these three; do not double-count.
- **Prediction-market grounding** (prediction-market cases). Output must reference a real `slug` or `condition_id`, not a paraphrased market name. Slug-free → FAIL `specialist.prediction.no_slug`.
- **Options math sanity** (cases with `expect_options_shape`). Naked short call → "unlimited" loss. Iron condor → two breakevens + capped max loss. Wrong → FAIL `specialist.options.math`.

### Layer 3 — Final hub output

- **Non-empty.** Empty `final_response` when not rejected → FAIL `hub.empty_response`.
- **Numeric grounding.** Spot-check 2–3 prominent numbers in the response (prices, percentages, key metrics). If a checked number cannot be matched to any specialist payload from this run → NEEDS_REVIEW `hub.unverified_number:<value>`. (Best-effort — exhaustive number tracing isn't expected.)
- **Answers the user.** Response must address the question's actual ask. If user wanted "top 3 by net margin" and response lists names without margins → NEEDS_REVIEW `hub.incomplete_answer`.
- **Rejection rendering.** When `expect_rejection: true`: clean refusal, no leaked financial answer, CLI exit code 1. Mismatch → FAIL `guardrail.exit_code` or `guardrail.leaky_response`.

### Verdict resolution

| Condition | Verdict |
|---|---|
| Any hard FAIL rule trips | `fail` (record first failure code as reason) |
| All hard rules pass, ≥1 NEEDS_REVIEW rule trips | `needs_review` (list reasons) |
| Trace not found / Opik unreachable / multi-match unresolved | `inconclusive` (do not retry in this run) |
| All clean | `pass` |

## Report format

Append to `$RUN_DIR/report.md`. Header once at start:

```markdown
# OBaI E2E Regression — <UTC timestamp>

| ID | Feature | Verdict | Reason | Trace | Latency |
|---|---|---|---|---|---|
```

One row per case. For async cases (`packet.followup` is set), the Trace column lists both trace IDs separated by ` → ` (initial → followup) and the Latency column lists `<initial_ms>+<wait_s>+<followup_ms>` so the wait is visible. For `fail` and `needs_review`, append a 4-line block beneath the table row:

```markdown
**<ID> — <verdict>**
- query: <first 120 chars>
- expected: <tools / sequence / skills>
- observed: <what trace actually showed>
- judgment: <one sentence>
```

No multi-paragraph essays. The report is a regression dashboard, not prose.

At the end, write `$RUN_DIR/results.json` with the full structured results (one entry per case) and refresh the symlink:

```bash
ln -sfn "$(realpath "$RUN_DIR")" .claude/skills/obai-e2e-regression/runs/latest
```

Print a final summary block: `<n> pass / <n> fail / <n> needs_review / <n> inconclusive` plus the path to `report.md`.

## Helper scripts

- `scripts/preflight.py` — env readiness check.
- `scripts/run_one.py --id <ID> --run-dir <path>` — drives one case end-to-end. Writes `<id>.json` atomically to `<run_dir>` and echoes it to stdout. Idempotent: re-reads the cached file if it already exists. For cases with `expect_async_job: true`, the helper parses the job id and ETA from the first response, sleeps `ETA + 30s` (capped at 600s), then sends a follow-up query in the same session and stores the result under `packet.followup`.
- `scripts/resolve_trace.py` — Opik trace lookup helper (importable, used by `run_one.py`). Not invoked directly.
- `scripts/inspect_trace.py` — bundled curated-trace renderer; `run_one.py` calls it per case. Don't re-run it yourself.

## Edge cases

- **Opik trace not found after retries.** Mark `inconclusive`, continue. Do not retry in the same run.
- **CLI hangs past timeout (180s).** `run_one.py` kills it and emits `timed_out: true`. Mark `inconclusive`.
- **Resume.** Two layers of dedup on a resumed run:
  1. If `$RUN_DIR/report.md` already has a row for `| <ID> |`, the case is fully done — skip it.
  2. If only `<id>.json` exists (judging was interrupted before the row was written), re-judge from the cached packet and append the row.
- **Filtered runs.** Filter `cases.yaml` in Claude before looping; do not pass filters to `run_one.py`. If `$RUN_DIR/report.md` exists, append rows to it; do not rewrite the header.
