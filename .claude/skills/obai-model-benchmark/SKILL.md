---
name: obai-model-benchmark
description: Compare hub orchestrator model/reasoning-effort combinations by running the paid OBaI E2E regression gate once per combo, then ranking them on reviewed quality, dollar cost, and latency. Use only when the user explicitly asks to benchmark, compare, or choose between hub models or reasoning efforts. This is N complete paid gate runs — a two-combo core benchmark costs twice a full release gate — so never invoke it as a side effect of a model or prompt change.
---

# OBaI Model Benchmark

Use this skill only when the user explicitly asks which hub `model:effort` configuration to ship, or asks to benchmark or compare specific combos. A request to read, edit, or test this skill authorizes offline work only — never suite execution.

This skill decides nothing on its own. It runs the existing black-box gate (`.claude/skills/obai-e2e-regression/`) once per combo under a pinned, provably identical source tree, then ranks the reviewed outcomes. The gate remains the authority on how a case is executed, judged, and reviewed; this skill only orchestrates repeats and compares them. Never edit the e2e skill.

Every combo is one full paid run. Two combos on `core` is two release gates. Disclose that arithmetic before anything paid starts.

## Scope limits

- Valid combos are `HUB_MODELS` x `HUB_REASONING_EFFORTS` from `core_agents.hub_settings` — today `{gpt-5.6-sol, gpt-5.6-terra}` x `{medium, high, xhigh, max}`. Anything else is rejected, not coerced.
- Tiers are `smoke` and `core` only. The `live` tier is refused: it is a provider-freshness canary whose outcomes move with the market, so it cannot separate two models.
- The hub is pinned per run by injecting `ORCHESTRATOR_MODEL` and `ORCHESTRATOR_REASONING_EFFORT` into each child process. Those outrank `~/.obai/settings.json` by design. **Never edit `~/.obai/settings.json` during a benchmark session** — the incumbent is resolved once, before any injection, and the gate binds that file into every run fingerprint. Changing it mid-session invalidates the comparison and can break resume.
- Recommendation only. Changing the shipped default is a separate, separately reviewed edit.

## 1. Configure the combos

Combos are user-configured, never inferred. Ask for them explicitly if the request names none. Typical is 2–3; the hard ceiling is 8, and 8 core runs is a cost decision the user must make deliberately. Duplicates are rejected.

Resolve the incumbent (`HubSettingsStore().load()`, file-or-default) and say what it is. Include it in the combo list whenever the point of the exercise is to change the default — without it there is no baseline, and the final report warns about its absence.

For a wider sweep, do not run every cell. Funnel: `smoke` across the candidate set to shake out harness and routing problems cheaply, then `core` on the **top two plus the incumbent**. Smoke qualifies; core decides. Never draw a shipping conclusion from smoke alone — 8 cases cannot separate two competent models.

## 2. Fill the prices file

Fill `config/model_prices.yaml` with the current USD-per-1M rates for every model that can appear in a span (both combo models plus the shipped specialist/guardrail models) before the final report. A null rate for an observed model is a hard error naming the model and the yaml path — the report will never assume zero. Cached input has its own rate; reasoning tokens bill as output and need no row.

## 3. Offline dry run first

Zero paid calls. This validates combos, tier, environment, and prints the plan:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-model-benchmark/scripts/benchmark_suite.py \
  --combos gpt-5.6-sol:high,gpt-5.6-terra:high --tier core \
  --session-dir <new-session-dir> --max-api-calls-per-combo 187 --dry-run
```

No mode flag also means dry-run. Nothing is written and no subprocess is spawned. Confirm the combo order, the tier, the per-combo cap, the total cap, and the incumbent line before proposing execution.

Exit 2 means validation or environment failure. The environment scrub is a hard gate: any inherited `*_MODEL`, `*_REASONING_EFFORT`, or `*_VERBOSITY` key is listed and refused, including a pre-set injected pair. Clear the offending variables in the shell; do not work around the check.

## 4. Paid execution boundary

Before executing, state: the exact combos in order, the tier and its case count, the per-combo `--max-api-calls`, and the total exposure (N x the per-combo cap). Repeat the gate's own caveat — `--max-api-calls` is a **between-case start limit**, not a hard cap, so one already-started agent can overshoot its estimate. An OpenAI project budget is the only hard backstop.

Get explicit authorization for that disclosed total. Authorization for one stage is not authorization for the next: a smoke pass does not authorize the core runs, and a completed benchmark does not authorize confirmation repeats. Ask again, with fresh arithmetic, each time.

## 5. Execute sequentially

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-model-benchmark/scripts/benchmark_suite.py \
  --combos gpt-5.6-sol:high,gpt-5.6-terra:high --tier core \
  --session-dir <new-session-dir> --max-api-calls-per-combo 187 --execute
```

Combos run one at a time into `<session-dir>/<model>@<effort>/`, each a normal gate run directory. Child output streams through. The orchestrator adds nothing to the gate's own preflight, snapshotting, or budget accounting; it records a `source_digest` of the runtime tree per combo so the final report can prove the code and prompts did not drift between runs.

Exit 0 is all combos complete, 1 is a combo failed and the session stopped there, 2 is validation or environment error. A failed combo stops the session by design — comparing a complete run against a truncated one is worse than having no comparison. Completion is read from each run's `results.json`, not from the gate's exit status: the gate exits 1 whenever a case is still `needs_semantic_review`, which is the expected state of every benchmark run before review, and 3 for provider noise that still produced a complete run. Only a gate exit 2 is fatal on its own.

Resume an interrupted session with `--resume-session --execute` and the identical combos, tier, cap, and session dir. Completed combos are skipped, a combo killed before it wrote `results.json` resumes through the gate's own `--resume`, and the rest run fresh. A combo that *published* an incomplete `results.json` (a cap abort, say) is refused with exit 2: that file is immutable and the gate's `--resume` replays it instead of executing, so the only way forward is a fresh session dir under new authorization. Never hand-edit session or run artifacts.

## 6. Review each run independently

Follow the e2e skill's "Required offline semantic closeout" section as written — read it, do not paraphrase it, and do not invent a benchmark-specific rubric. The rubric and the `semantic_reviews.json` schema live there and are identical here.

Review each run **blind to the others**. Read the packet, response, spans, and parent packets for that run only, and judge each unexecuted assertion on its own evidence. A combo's reputation, price, or the fact that another combo answered differently is not evidence. Never infer `pass` from plausible prose.

Draft `<session-dir>/<model>@<effort>/semantic_reviews.json` for every run before running the audit. Do not finalize yet.

## 7. Audit the drafts across combos

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-model-benchmark/scripts/benchmark_report.py \
  --session-dir <session-dir> --audit
```

Offline, no scoring, no prices. It writes `audit.json` and `audit.md`: a per-case verdict matrix across combos plus a disagreement worklist — every case whose deterministic verdict or draft assertion status differs between combos, with each combo's `packet_path`.

A draft whose shape the audit cannot read is exit 2 naming the file and the row, never a silently empty matrix: an audit that reports "no disagreements" because it parsed nothing is worse than no audit.

## 8. Settle disagreements side by side

For every worklist entry, re-read the listed packets against each other and decide which draft was wrong. Cross-combo disagreement is the strongest available signal that a review was sloppy: identical inputs, identical code, different judgments. Edit the drafts so each one stands on its own evidence. A disagreement that survives honest re-reading is a real quality difference and must stay.

Re-run `--audit` after edits if the worklist changed materially. This step is what makes the comparison trustworthy; do not skip it because the drafts "look fine."

## 9. Finalize each run

Run the gate's finalizer once per combo run dir:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/finalize_review.py \
  --run-dir <session-dir>/<model>@<effort>
```

It authenticates and recomputes everything offline and writes immutable `reviewed-results.json`. The final report refuses preliminary-only runs, so every combo must be finalized.

## 10. Final report

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-model-benchmark/scripts/benchmark_report.py \
  --session-dir <session-dir>
```

Writes `benchmark.json`, `benchmark.md`, and appends one line to `.e2e-runs/benchmarks/ledger.jsonl`. Exit 0 report written, 2 config or artifact error, 3 a fairness or took-effect violation.

Exit 3 is not a formatting problem and is never to be worked around. Either the runs are not comparable — differing cases snapshot, suite fingerprint, dirty flag, or source digest; a git SHA that differs while the byte-level source digest and dirty flag both match is the one bookkeeping case (a mid-session commit of already-present bytes) and demotes to a warning — or a combo did not take effect, meaning some hub span ran a model or effort other than the one that combo claims, or a combo contributed no hub span at all (its packets or trace evidence are gone, so its $0.00 cost would win every tiebreak on evidence nobody read). Fix the cause and rerun the affected combo under fresh authorization.

Individual cases whose trace evidence was never captured are not fatal: they are reported as warnings, contribute no spans or cost, and are already undecided, so they fall outside the scored intersection.

## Reading the scoreboard

- **Ranking** is lexicographic over the intersection of cases decided in every combo: `strict` (count of `pass`) descending, then `total` (`pass` + `pass_degraded`) descending, then dollar cost ascending, then median latency ascending. Quality first; cost and latency only break quality ties. A pair still tied after all four is reported as a tie, not silently ordered.
- **Undecided cases** (`inconclusive_*`, `skipped_dependency`) are excluded from the intersection and listed per combo. Read that list: a combo that is "winning" on twelve cases while five were inconclusive has not won anything yet.
- **Safety disqualifier**: any `fail_product` on a case whose feature contains `guardrail` disqualifies that combo from recommendation regardless of its score. It stays in the table flagged `DISQUALIFIED`. Do not argue it back in on cost.
- **Podium rule**: when the number of cases outside the intersection is greater than or equal to the strict-score gap between the top two *recommendable* combos, the ranking is marked not decision-grade, the arithmetic is printed, and no recommendation line is emitted. That is the correct outcome, not a failure — report what to rerun rather than picking a winner the evidence cannot support. Disqualified combos are skipped in that arithmetic (their score can never support a recommendation), and a session that decided every case in every combo is always decision-grade — with nothing outside the intersection there is nothing left to rerun, so an equal-quality cost tiebreak stands.
- **Incumbent warning**: if the incumbent was not in the combo list, the report says so. Treat any comparison without it as informational.

## Repeats before flipping the default

A single gate run per combo is one sample of a stochastic system. When the recommendation differs from the incumbent, the report states that confirmation repeats are required — that is guidance the report cannot enforce, so enforce it here.

Run the top combo and the incumbent again on the same tier, in a **new session dir**, under separately disclosed and authorized cost. If the winner changes between sessions, the two are not distinguishable at this sample size: report that honestly instead of shipping the luckier run. Compare across sessions only when the byte-level source digest matches.

## Handoff

Report: the combos, tier, and session dir; the scoreboard with strict, total, cost, and median latency per combo; every disqualification, exclusion, and warning; whether the podium rule suppressed the recommendation; the artifact paths (`benchmark.md`, `audit.md`, each run's `reviewed-results.json`); and observed spend against the disclosed cap.

Then stop. Recommend a combo, name the confirmation repeats it still needs, and leave the shipped default alone. Changing `~/.obai/settings.json` or a hub default in code is a separate request, made after the user has seen this report.
