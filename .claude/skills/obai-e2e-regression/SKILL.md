---
name: obai-e2e-regression
description: Run the cost-aware OBaI black-box regression gate after substantive prompt, routing, specialist, or subagent changes. Use only when the user explicitly requests OBaI E2E regression, merge validation, or a full regression run. Default to the deduplicated core tier; run the live canary or separate broader evaluation corpus only when explicitly requested because they consume additional billable model requests.
---

# OBaI E2E Regression

Use this skill only when the user explicitly requests an OBaI E2E/full regression run or merge gate. Do not invoke paid OBaI queries merely because code, prompts, routing, or subagents changed. A request to inspect, edit, or review this skill authorizes offline validation only—not suite execution.

This is a black-box release gate. It submits real CLI queries, correlates each query to one complete Opik trace, checks raw spans deterministically, and then closes qualitative financial assertions through an evidence-backed offline review. Never treat a provider outage, missing evidence, stale checkpoint, or judge uncertainty as a pass.

## Canonical sources

- Paid gate: `cases/cases.yaml` — 37 deduplicated cases.
- Pre-change backup: `cases/cases.v1-2026-07-15.yaml` — 88 cases; never execute as the default gate.
- Broader/overlapping coverage: `src/obai/evaluation/test_cases/suite.yaml` — separate evaluation corpus, not valid input to this skill's canonical runner.
- `.agents/skills/obai-e2e-regression/SKILL.md` is only a compatibility pointer. Use this directory's scripts and cases.

The exact tier budgets below are enforced in both directions: deleting a case
or reducing its estimate fails lint just as an overrun does.

| Tier | Cases | Minimum planning estimate | Selection |
|---|---:|---:|---|
| `smoke` | 8 | 45 | Explicit cheaper route check |
| `core` | 21 | 187 | Exact default release gate |
| `live` | 8 | 48 | Explicit provider/freshness canary |

The legacy YAML/CLI field is named `estimated_api_calls`, but it is only a minimum planning estimate for billable model requests, including guardrail, hub, skill-load continuation, and specialist turns. `--max-api-calls` is a **between-case start limit**, not a hard cap: one already-started hub or specialist agent can exceed its estimate before control returns to the runner. The runner counts actual Opik `llm` spans after every case and refuses to start another case when accounting is unavailable or the next estimate would cross the limit. Use an OpenAI project budget/rate limit as the hard external spending backstop.

## Offline validation and planning

Run these before proposing or executing a suite. They make zero OBaI/model/provider calls:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/lint_cases.py \
  .claude/skills/obai-e2e-regression/cases/cases.yaml --strict
```

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/run_suite.py \
  --dry-run --run-dir <new-run-dir>
```

No mode flag also means dry-run. Confirm `attempted_count: 0`, the exact selected IDs, estimated model requests, and dependency closure. Dry-run artifacts must never be resumed as paid runs.

## Paid execution boundary

Before execution, tell the user the exact tier, case count, minimum estimate, and between-case limit—including the in-flight overshoot limitation. Execute only if their request clearly authorizes the paid run. Every paid execution requires an explicit `--max-api-calls`. Use a new run directory.

Core gate:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/run_suite.py \
  --execute --max-api-calls 187 --run-dir <new-run-dir>
```

Smoke gate, only when the user asks for smoke/cheaper coverage:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/run_suite.py \
  --execute --tier smoke --max-api-calls 45 --run-dir <new-run-dir>
```

Live canary, only when explicitly requested:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/run_suite.py \
  --execute --tier live --allow-expensive --max-api-calls 48 \
  --run-dir <new-run-dir>
```

`--allow-expensive` is the canonical live-tier opt-in; it does not disable the between-case limit. An `--id` selection automatically includes chain parents; disclose the closed plan before executing. Do not use `repeat`; repeated/stochastic coverage belongs in the broader evaluation corpus.

Execution is serial. `run_suite.py` automatically runs the zero-model-call preflight before the first case. It stops on broken trace/cost accounting, enforces complete and stable raw-span snapshots, limits async cases to their declared paid polls, and preserves raw CLI/trace evidence. It never calls an LLM judge: every paid `obai` subprocess is launched with `ENABLE_INLINE_SCORING=false`, even if an inherited or CLI-managed setting tries to enable it.

Preflight resolves the inherited environment plus the CLI-managed `~/.obai/.env` with the same no-override precedence as `obai`. That effective environment is shared with `obai status` and paid query subprocesses, so Opik/model/MCP settings cannot point the helper and CLI at different services. `OPENAI_API_KEY` may come from either source; an inherited value has precedence. The key is never printed or copied into run artifacts.

Before a paid subprocess can start, the runner writes `<run-dir>/cases.snapshot.yaml` once, binds its SHA-256 and path in the manifest, and passes only that snapshot to `run_one.py`. The manifest also binds the exact runner/preflight/judge-packet paths and bytes, prompt/runtime tree, the content digest of `~/.obai/.env`, effective model/MCP/cache/Opik/base-URL settings, `~/.obai/preferences.json`, `~/.obai/settings.json`, and domain-separated digests of active secret settings including the *effective* OpenAI credential (inherited environment first, then `~/.obai/.env`); no secret value is serialized. The snapshot, manifest, runtime, helper bytes, preferences, hub settings, and credential identity are rechecked before every case, and `run_one.py` rechecks its full input fingerprint before the initial request and each async poll.

`~/.obai/settings.json` is where the web UI and `obai config` store the user-chosen hub model and reasoning effort, so binding it is what stops a hub swap from producing a byte-identical fingerprint and replaying a cached result for a configuration that never ran. An absent file is the normal state and hashes identically everywhere. Precedence is unchanged: `ORCHESTRATOR_MODEL` and `ORCHESTRATOR_REASONING_EFFORT` in the environment still outrank the file, so the gate can pin the hub by injecting those variables, and both the injected value and the file it overrides stay bound in the fingerprint.

Each case receives a cryptographically random 256-bit nonce in an fsynced immutable attempt marker. `run_one.py` will not run from `--id`/`--run-dir` alone: it must validate the execute manifest, snapshot, run ID, marker, and nonce, then atomically consume the nonce in `<run-dir>/claims/` before the first model request. Packets and judgments bind the attempt, claim, manifest, snapshot, and input fingerprints. The old `.agents/.../scripts/run_one.py` entry is deliberately disabled. Never edit, copy, or replace run-directory artifacts.

## Separate broader evaluation corpus

The 210-case `src/obai/evaluation/test_cases/suite.yaml` corpus is **not** compatible with `run_suite.py` and must never be passed as its `--cases` file. Its 25 `extended_only` cases are excluded by default, leaving 185 default rows. Preview that corpus offline from `src/obai`:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python -m evaluation \
  list-tests --include-extended
```

Execute it only after the user separately authorizes the selected IDs and the additional model/provider and LLM-judge cost. Use the evaluation CLI's explicit `--include-extended` flag only when needed, and prefer a surgical `--ids` selection plus `--limit`. This path does not inherit the canonical runner's trace-counted between-case limit or resume ledger, so disclose that limitation before running it.

Every expected-success row and scoped partial-refusal row requires semantic scorers; the evaluation runner refuses `--no-builtin` rather than reporting routing, response length, regex matches, or coincidentally matching numbers as an accuracy pass. `--no-builtin` is limited to selected deterministic rejection, no-data, or specialist-error contracts. A dedicated partial-refusal judge verifies every refused scope, blocks fabricated results and side effects, and accepts equivalent refusal wording; its regexes are diagnostics only. Semantic scorers are N/A—not false failures—for deterministic degraded/error branches and are not invoked for guardrail rejections. Before execution, the loader validates a closed case schema, selection/scorer metadata, output destinations, and a non-empty `ANTHROPIC_API_KEY` whenever the selected rows require judges. During execution, each remote Opik row must exactly match its locally selected query contract before the query runner is called. At aggregation, the locally computed scorer plan—not a list self-reported by the result—must be complete, with a non-skipped literal Outcome verdict. Include judge cost whenever authorizing success or partial-refusal cases.

For `evaluation evaluate --suite`, exit `0` means every selected row passed, `1` means at least one captured contract failed, `2` means selection/configuration was invalid, and `3` means scoring was incomplete or errored. A printed report is never a substitute for checking the exit status.

## Resume

Resume only an interrupted paid run, using the same tier/IDs, cap, cases file, runtime, prompts, model settings, and explicit run directory:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/run_suite.py \
  --execute --resume --max-api-calls <same-between-case-limit> --run-dir <existing-run-dir>
```

For the live tier, repeat `--tier live`, `--allow-expensive`, and the same explicit limit. Resume fails closed when case/runtime/credential/helper fingerprints, run IDs, attempt nonces, consumed-claim records, packet digests, or recomputed deterministic judgments differ, or when a live/relative run exceeds its shortest freshness SLA. An immutable attempt marker is written before each paid subprocess and its nonce is consumed before the first request. If interruption leaves a consumed attempt with no trustworthy packet, resume stops instead of risking a duplicate paid call; investigate or start a separately authorized new run. Never copy checkpoints into a new run or bypass a rejected resume.

## Multi-turn coverage

The canonical suite exercises real same-session chains, not prose that merely pretends a prior turn occurred:

- four-stage crypto backtest → stored-log inspection → conditional export → artifact validation;
- three-stage screen → cross-specialist rank/research → constrained allocation;
- live Polymarket search → exact-slug execution memo;
- live earnings date → collar selection → collar/portfolio risk.

Children reuse the verified parent session and checkpoint. They may continue after `needs_semantic_review` so the stateful path is actually tested, but never after product/harness/infrastructure failure. `chain_requires_parent_outcomes` makes conditional children `skipped_dependency` when the parent took an accepted but inapplicable branch, such as `data_unavailable` or `partial_refusal`.

For `relative` and `live` cases, the manifest records one suite-wide calendar anchor instant. Every root turn derives `today`, `tomorrow`, and the current year in that case's IANA timezone from the same anchor; the runner records the resulting calendar context in the packet and reuses it throughout the same-session chain. This prevents different roots from silently crossing midnight while still respecting their declared local timezones. Words such as `latest` and `now` remain explicit live-retrieval requirements.

## Deterministic results

`results.json` and per-case judgments are immutable preliminary artifacts. Interpret verdicts exactly:

- `pass` / `pass_degraded`: all declared deterministic checks passed.
- `fail_product`: captured evidence violates a hard product contract.
- `needs_semantic_review`: deterministic checks passed, but one or more listed financial/qualitative assertions remain.
- `inconclusive_provider`, `inconclusive_harness`, `inconclusive_missing_evidence`: no pass claim is allowed.
- `skipped_dependency`: no paid child call occurred because its parent branch or verdict made it inapplicable.

Every `required_text` / `forbidden_text` spec declares a `kind`. A `structural`
spec pins a phrasing-independent fact — a ticker, currency code, ISO date,
identifier, number, or a field name the product contractually emits — and a
miss is a hard `fail_product`. A `lexical` spec pins an English phrasing of a
free-form answer; a miss records a `diagnostics` entry and adds an unexecuted
assertion, so the case routes to `needs_semantic_review` and the offline
reviewer decides whether the property holds in substance. An absent `kind` is
read as `structural`, so an unclassified spec keeps the hard-gate behavior;
`--strict` lint requires the key so the corpus cannot drift back.

This split exists because the deterministic layer cannot tell a wrong answer
from a differently-worded right one. Across eight runs, 55 of 73 failed checks
were a correct answer phrased differently than a regex expected, and each fix
widened one regex until the next run found a new sentence. Never resolve a red
`lexical` diagnostic by widening its regex — either the reviewer confirms the
substance and the case passes, or the substance is genuinely missing and the
review fails it.

Unexpected undeclared financial specialists are listed for semantic review because they can indicate both routing drift and excess spend. Any financial-specialist error span is a failure or provider-inconclusive result, including errors from optional routes. An explicit top-level structured error in a specialist or required-skill output (`isError: true`, `is_error: true`, or exact status `error`/`failed`/`failure`) is treated like `error_info`, including when the output is a bounded JSON-object string; generic `error` keys and prose are not. For async cases, the declared financial specialist and required routing skill must execute successfully on the initial turn and every poll, and errors from any executed turn remain authoritative even though response assertions use the final completed poll. `cost.max_specialist_calls` is enforced separately on every authoritative CLI-turn trace (the initial turn and each async poll): each exact outer financial-specialist span counts, including `allowed_extras`, while nested LLM/provider spans do not. Missing raw spans make these checks evidence-inconclusive rather than falling back to curated or CLI claims.

## Required offline semantic closeout

Do not rerun a case to judge it. Review every result that still carries `unexecuted_assertions`: both `needs_semantic_review` and `fail_product`. A deterministic failure does not cancel a case's remaining assertions, and skipping them lets a second, unrelated defect ship inside a case that is already red. Semantic findings can only add to a deterministic failure, never overturn it.

For each, read its `packet_path`, the full response, raw trace/spans, parent packets for chains, and every exact string in `unexecuted_assertions`. Recompute material arithmetic and verify timestamps, units, source lineage, premise checks, missing-data handling, and cross-turn identity from captured evidence.

Write `<run-dir>/semantic_reviews.json` with this shape. Copy `run_id`, `case_fingerprint`, `packet_sha256`, and assertion strings exactly from the preliminary artifacts. Every assertion needs at least one real packet-field reference; a prose-only evidence string is rejected:

```json
{
  "schema_version": 2,
  "run_id": "<results.run_id>",
  "reviews": [
    {
      "case_id": "<case-id>",
      "case_fingerprint": "<result.case_fingerprint>",
      "packet_sha256": "<result.packet_sha256>",
      "summary": "Concise evidence-backed conclusion.",
      "assertions": [
        {
          "assertion": "<exact unexecuted_assertions entry>",
          "status": "pass",
          "evidence": {
            "analysis": "Explain the recomputation and why the cited evidence supports it.",
            "references": [
              {
                "case_id": "<current-or-parent-case-id>",
                "packet_sha256": "<that result.packet_sha256>",
                "json_path": "trace.spans[3].output",
                "span_id": "<optional exact span id>"
              }
            ]
          }
        }
      ]
    }
  ]
}
```

Use `status: fail` for a disproved assertion and `status: inconclusive` when captured evidence cannot establish it. Never infer `pass` from plausible prose. Before applying reviews, the finalizer authenticates the preliminary summary against the execute manifest, exact cases snapshot, attempt ledger, packet SHA-256 bindings, and per-case judgments, then recomputes every deterministic judgment offline. It also rejects missing, extra, duplicated, stale-fingerprint, stale-run, invented-path/span, or prose-only decisions. It makes no API calls:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/finalize_review.py \
  --run-dir <run-dir>
```

This preserves `results.json` and writes immutable `reviewed-results.json`. A failed semantic assertion becomes `fail_product`; insufficient evidence stays `inconclusive_missing_evidence`; only fully evidenced assertions can become `pass` or `pass_degraded`.

## Report and handoff

Render after semantic finalization; the renderer automatically prefers `reviewed-results.json`:

```bash
UV_CACHE_DIR=/tmp/obai-uv-cache uv run python \
  .claude/skills/obai-e2e-regression/scripts/render_report.py \
  --run-dir <run-dir>
```

This writes two human-readable reports from the same structured artifacts:
`<run-dir>/report.md` — a greppable markdown dashboard (`| ID | Feature |
Verdict | Reason | Trace | Latency |` row per case, plus a compact evidence
block under each non-pass case) — and `<run-dir>/report.html` — the styled
dashboard (stat cards, verdict bar, per-case cards). Cite both paths in the
handoff. Do not use `--open` unless the user asks to open the browser. Report selected tiers, planned/attempted/skipped counts, estimated and observed model requests, every final verdict, abort reason, and artifact paths. Exit codes are `0` clean, `1` product failure or pending semantic review, `2` configuration/review artifact error, and `3` infrastructure/provider/missing-evidence incompleteness.

## Maintaining cases

Keep the paid corpus surgical. Each case must protect a distinct routing, quantitative correctness, source-grounding, freshness, safety, or state-handoff invariant. Use `live`/`relative` policy and an explicit timezone/freshness SLA for current requests; use old years only for intentional frozen historical windows with a versioned data contract, oracle, or immutable fixture. Replace unverified market premises with a premise check or conditional branch. Fully specify valuation conventions, costs, timestamps, portfolio constraints, and no-trade/refusal behavior so a real retail investor, quant analyst, or hedge-fund reviewer can tell correct from merely fluent.

Classify every new `required_text` / `forbidden_text` spec as `structural` or `lexical` when you add it — `--strict` lint fails otherwise. Prefer a structural assertion: pin the ticker, the number, the identifier, or the emitted field name rather than the sentence the model wraps around it. Reach for `lexical` only when the property genuinely has no phrasing-independent form, and pair it with a `manual_assertions` entry that states the property in full, because the reviewer, not the regex, is what decides that case.

Move near-duplicates, broad variants, and stochastic repeats to `src/obai/evaluation/test_cases/suite.yaml`; do not increase paid coverage unless it adds a materially new failure mode. In that broader corpus, every financial-data, backtest, portfolio, prediction-market, or research case must declare `live`, `relative`, or `frozen`; only genuinely timeless routing/capability checks may omit the policy. `live` requires a recent explicit as-of/provider timestamp, while `relative` binds interpretation to the trace's evaluation-time anchor without incorrectly treating legitimate historical or future horizon dates as stale quotes. Date scoring is not applicable to a correctly declared no-data/refusal/error outcome.
