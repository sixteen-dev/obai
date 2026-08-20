# Why the Hub defaults to `gpt-5.6-terra` at `max` effort

OBaI ships the Central Hub on `gpt-5.6-terra` at `max` reasoning effort. This
note records how that default was chosen, what the evidence actually showed,
and what is still open.

## What we compared

We ran the paid E2E regression gate (`core` tier, 21 cases) once per hub
combination, holding the case snapshot, prompts, runtime, and specialist models
fixed and pinning only the hub via `ORCHESTRATOR_MODEL` /
`ORCHESTRATOR_REASONING_EFFORT`:

- `gpt-5.6-terra:max`
- `gpt-5.6-sol:medium`

Every case was judged by the gate's deterministic checks plus the offline
evidence-backed semantic review. No LLM judge scored the runs inline.

## Result

| Combo | Strict passes | Total score | Cost (USD) | Median latency (ms) |
| --- | ---: | ---: | ---: | ---: |
| `gpt-5.6-terra:max` | 10 | 18 | 1.1590 | 24497 |
| `gpt-5.6-sol:medium` | 9 | 17 | 1.8297 | 25128 |

Scored over the 19 cases both combos decided. `CORE-FX`
(`inconclusive_provider`) and `CORE-MS-ALLOCATE` (`skipped_dependency`) were
excluded for both.

Quality was **on par** — the two combos returned identical verdicts on 18 of 19
decided cases. The single divergence was `CORE-CRYPTO-EXPORT`, which
`terra:max` passed and `sol:medium` failed (`fail_product`). Both failed
`CORE-CRYPTO-VALIDATE`.

## The cost result is the counterintuitive part

The intuition going in was that the deeper combo would cost more. It did not.
`terra:max` came in at **63% of the cost** of `sol:medium` ($1.1590 vs
$1.8297) and was marginally faster at the median. The reasonable reading is
that a stronger hub at high effort resolves routing and synthesis in fewer,
better turns, while the cheaper-per-token combo spends its savings back on
retries, re-routing, and longer specialist fan-out. Per-token price is not the
same as per-query price when the hub controls how much downstream work happens.

That is the case for the default: on this corpus `terra:max` was at least as
correct, cheaper, and no slower. There was no trade to make.

## Caveats — this is not decision-grade on its own

The benchmark report flags itself as not decision-grade, and we are not going
to paper over that:

- Two cases (`CORE-FX`, `CORE-MS-ALLOCATE`) fell outside the decided
  intersection. The strict-score gap is 1. Those two cases could reorder the
  podium if they resolved.
- The two combo runs span two UTC calendar days (2026-08-15, 2026-08-16), so
  live market data moved between them.
- The git tree was dirty for both runs; the fingerprinted source tree was
  byte-identical across combos (`source_digest` equal), but neither run
  corresponds to a clean commit.
- `n = 1` per combo. The gate is serial and non-repeated by design; stochastic
  coverage lives in the broader evaluation corpus.

So the default is chosen on a consistent-but-narrow signal plus the fact that
`terra:max` had no measured downside. It is not chosen on a statistically
settled margin.

## What would change our mind

Rerunning the two excluded cases for both combos on the same snapshot, and
regenerating the report. If `CORE-FX` and `CORE-MS-ALLOCATE` resolve in
`sol:medium`'s favor, the strict gap closes and the cost argument becomes the
only argument — still a real one, but worth restating explicitly.

## It is a default, not a lock-in

The hub model and effort are the two knobs intended to be changed without
editing code. Precedence is `ORCHESTRATOR_MODEL` /
`ORCHESTRATOR_REASONING_EFFORT` → `~/.obai/settings.json` → shipped default.
Change it from the `obai web` settings modal, or:

```sh
obai config set-model gpt-5.6-sol
obai config set-effort high
```

Specialist models stay code-owned and are tuned through environment variables.
