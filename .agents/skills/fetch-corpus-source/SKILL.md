---
name: fetch-corpus-source
description: Acquire raw source artifacts for the OBaI knowledge base from a specified source in the manifest below. Use this skill when the user invokes /fetch-corpus-source <source_id> or asks to acquire/download corpus source data. The skill reads the per-source acquisition intent, uses WebSearch + WebFetch to locate canonical artifacts, saves them under src/knowledge-base-server/sources/<source_id>/, and records progress in state/source_acquisition_progress.csv. Skips sources marked decision_pending, deferred_v2, or manual_purchase unless explicitly enabled by the user.
---

# Fetch Corpus Source

Acquire raw source artifacts that the OBaI knowledge base drafting skill (`/draft-corpus-entry`) will later consume.

## Paths

All paths in this skill resolve relative to the **OBaI repo root**, determined at invocation time:

```sh
REPO_ROOT="$(git rev-parse --show-toplevel)"
```

The skill must be invoked from somewhere inside the OBaI git checkout. If `git rev-parse --show-toplevel` fails (not a git repo) or the resulting path's basename is not `obai`, abort with an explicit error rather than guessing.

Concrete anchors used by this skill:

- **Sources root:** `$REPO_ROOT/src/knowledge-base-server/sources/`
- **State root:** `$REPO_ROOT/src/knowledge-base-server/state/`
- **Progress CSV:** `$REPO_ROOT/src/knowledge-base-server/state/source_acquisition_progress.csv`

Each manifest entry's `place_at` value is relative to `$REPO_ROOT/src/knowledge-base-server/` (e.g. `place_at: sources/openap/` resolves to `$REPO_ROOT/src/knowledge-base-server/sources/openap/`). Create parent directories as needed; do not assume they exist.

Never write outside `$REPO_ROOT/src/knowledge-base-server/sources/` or `$REPO_ROOT/src/knowledge-base-server/state/`. The skill does not touch `corpus/`, `corpus_private/`, or anywhere else in the tree.

## When invoked

You are invoked with a source id from the manifest in this file: `/fetch-corpus-source <source_id>`.

If the user passes `all` instead of a single id, iterate every manifest entry whose `status: included_v1` and whose `access_type` is not `manual_purchase` or `manual`. Use the existing `/loop` skill if the user wants this paced rather than sequential in one turn.

## Your job

1. **Look up the source** in the manifest below by its `id`.
2. **Gate decisions** before any fetching:
   - If `status: decision_pending` → report `skipped: decision pending — user must explicitly enable` and exit.
   - If `status: deferred_v2` → report `skipped: deferred to v2` and exit.
   - If `access_type: manual_purchase` → report `skipped: copyrighted material, user acquires legitimately` and exit. **Do not fetch even if PDFs are findable on the web.**
   - If `access_type: manual` → report `skipped: user-authored artifact, no fetch needed` and exit.
   - If `access_type: paid_api` and no credentials are configured → report `skipped: paid API, credentials not configured` and exit.
3. **Fetch** for `access_type: open` (and `paid_api` when credentials are present):
   - Read `what_to_acquire` for the natural-language acquisition intent.
   - Use WebSearch and/or WebFetch to locate the canonical source. If a primary URL has moved or 404s, search for the current canonical location before giving up. Log the URL change in your progress row.
   - Download artifacts. For `partial_paywall`, fetch only the open portions described in `what_to_acquire`; do not attempt to bypass paywalls.
   - Save to `$REPO_ROOT/src/knowledge-base-server/<place_at>` (see **Paths** section above).
   - Verify the downloaded artifact's format matches `format_expected`. If a PDF lands as HTML or vice versa, treat as failed.
4. **Record progress** by appending one row per artifact to `$REPO_ROOT/src/knowledge-base-server/state/source_acquisition_progress.csv` with columns: `source_id, artifact_path, content_sha256, fetched_at_iso, notes`. Store `artifact_path` as a path relative to `$REPO_ROOT` so the CSV stays portable across checkouts. Create the file (and the `state/` directory) with a header row if it doesn't exist.

## Reporting

After completing (or skipping), output a one-paragraph summary:

- **Source id**
- **Status**: `fetched | skipped | failed`
- **Files placed** (paths, one per line if multiple)
- **Notes** (URL changes encountered, partial fetches, error messages)

## Hard constraints

- **Do NOT bypass paywalls.** Sources marked `access_type: paid_api` require credentials and TOS compliance.
- **Do NOT fetch copyrighted books** even if PDFs are findable. `manual_purchase` is non-negotiable.
- **Do NOT auto-ingest from aggregator sites** (Quantocracy, Robot Wealth). Use them only as discovery aids to find canonical sources by name.
- **Do NOT modify the manifest below.** If a source needs updating, tell the user to edit this file directly.
- **Do NOT touch `corpus/` or `corpus_private/`.** Your job is acquisition of raw sources, not drafting derived entries. Drafting is `/draft-corpus-entry`'s job.

---

# Source Acquisition Manifest

This is the source of truth for what gets acquired and where it lands. Each entry includes a `corpus_destination` field indicating whether the derived corpus entries (produced later by `/draft-corpus-entry`) go to the public `corpus/` (committed to git) or private `corpus_private/` (gitignored, personal use only).

## Status legend

- `included_v1` — fetch as part of v1 corpus build.
- `optional_supplement` — process if local artifacts are present at `place_at`; skip silently if absent. Non-blocking. Used for manual-purchase sources where the user may or may not provide content.
- `deferred_v2` — explicitly deferred to v2. Listed in "Deferred sources" at the bottom, not in the active manifest.
- `manual_only` — user provides the artifact directly (no automated fetch needed, e.g., the concept seed glossary).

## Corpus destination legend

- `public` — derived corpus entries land in `corpus/` (committed to open-source repo).
- `private` — derived corpus entries land in `corpus_private/` (gitignored, personal use only).

---

## Tier 1 — Foundational equity anomaly catalogs

### openap

- **status:** `included_v1`
- **access_type:** `open`
- **corpus_destination:** `public`
- **what_to_acquire:** Open Source Asset Pricing (Chen & Zimmermann). Two artifact sets: (1) the signal metadata catalog — one row per anomaly with formula, citation, category; (2) per-signal portfolio return time series (long, short, long-short spreads). Both are CSV. Distributed via the project's public GitHub repository and website.
- **place_at:** `sources/openap/`
- **format_expected:** csv. Expect `SignalDoc.csv` (or similar) at the root, and per-signal return CSVs under `portfolios/`.
- **expected_volume:** ~200 strategies
- **notes:** Open license. Cite Chen & Zimmermann in derived corpus entries. Record the source's release version or commit hash on acquisition so re-runs can detect updates.

### hxz_replication

- **status:** `included_v1`
- **access_type:** `partial_paywall` (paper paywalled; appendix tables circulate openly)
- **corpus_destination:** `public`
- **what_to_acquire:** Hou-Xue-Zhang "Replicating Anomalies" (Review of Financial Studies, 2020). Paper itself is paywalled. Appendix tables listing 452 anomalies and their replication outcomes are widely circulated as supplementary materials. Goal: get the anomaly list with replication verdicts, NOT the full paper text.
- **place_at:** `sources/hxz/`
- **format_expected:** pdf or csv (depends on how supplementary materials are distributed)
- **expected_volume:** Merge with OpenAP (dedupe); net add 0-50 strategies plus replication metadata enriching existing entries
- **notes:** Used primarily for the `known_failure_modes` and decay evidence fields on entries already in the corpus from OpenAP, not as a standalone strategy source.

### fama_french

- **status:** `included_v1`
- **access_type:** `open`
- **corpus_destination:** `public`
- **what_to_acquire:** Ken French's data library at Tuck (Dartmouth). Specifically: 3-factor model data, 5-factor model data, and the momentum factor data. Available as CSV inside ZIP archives.
- **place_at:** `sources/fama_french/`
- **format_expected:** csv inside zip. First few rows are header metadata — skip when parsing.
- **expected_volume:** ~10 foundational factor entries (market, SMB, HML, RMW, CMA, momentum)
- **notes:** Free for academic and research use; cite Ken French.

## Tier 2 — Practitioner books and research firms

### ernie_chan

- **status:** `optional_supplement`
- **access_type:** `manual_purchase`
- **corpus_destination:** `private`
- **what_to_acquire:** Three Ernie Chan books — *Quantitative Trading* (2nd ed., 2021), *Algorithmic Trading: Winning Strategies and Their Rationale* (2013), *Machine Trading* (2017). User acquires legitimately and places PDFs/EPUBs as-is.
- **place_at:** `sources/ernie_chan/`
- **format_expected:** pdf or epub
- **expected_volume:** ~30 strategies (mean reversion, pairs, microstructure, intraday) — only if user provides PDFs
- **notes:** DO NOT fetch via AI search — copyrighted. Non-blocking: if `sources/ernie_chan/` is empty, drafter skips this source silently. If user provides PDFs, drafter processes them and derived entries land in `corpus_private/`.

### lopez_de_prado

- **status:** `optional_supplement`
- **access_type:** `manual_purchase`
- **corpus_destination:** `private`
- **what_to_acquire:** *Advances in Financial Machine Learning* (2018) and *Machine Learning for Asset Managers* (2020). User acquires legitimately.
- **place_at:** `sources/lopez_de_prado/`
- **format_expected:** pdf or epub
- **expected_volume:** ~15 entries (meta-labeling, triple barrier, fractional differentiation, ensemble patterns) — only if user provides PDFs
- **notes:** Manual purchase only, same as Ernie Chan. Non-blocking: drafter skips silently if `sources/lopez_de_prado/` is empty. Derived entries go to `corpus_private/` when present.

### aqr_research

- **status:** `included_v1`
- **access_type:** `open`
- **corpus_destination:** `public`
- **what_to_acquire:** AQR Capital Management's research library. Public PDFs covering factor investing across asset classes (carry, value, momentum, defensive). Focus on multi-asset and practitioner-flavored papers, not academic re-publications.
- **place_at:** `sources/aqr/`
- **format_expected:** pdf
- **expected_volume:** ~40 strategies
- **notes:** Free public access. Start with multi-asset factor and carry-trade papers.

### sinclair_natenberg

- **status:** `optional_supplement`
- **access_type:** `manual_purchase`
- **corpus_destination:** `private`
- **what_to_acquire:** Euan Sinclair *Volatility Trading* (2nd ed., 2013) and Sheldon Natenberg *Option Volatility and Pricing* (2nd ed., 2014). User acquires legitimately.
- **place_at:** `sources/sinclair/` and `sources/natenberg/`
- **format_expected:** pdf
- **expected_volume:** ~25 strategies (options vol patterns) — only if user provides PDFs
- **notes:** Manual purchase. Primary source for options-vol section; without these the options coverage is thin but v1 is still shippable. Non-blocking: drafter skips silently if directories are empty.

### cboe_methodology

- **status:** `included_v1`
- **access_type:** `open`
- **corpus_destination:** `public`
- **what_to_acquire:** CBOE white papers and methodology documents for systematic options indices: BuyWrite (BXM), PutWrite (PUT), CollarIndex, S&P 500 30-Delta BuyWrite (BXMD), and similar. Public PDFs.
- **place_at:** `sources/cboe/`
- **format_expected:** pdf
- **expected_volume:** ~15 strategies
- **notes:** Open access. Complements Sinclair/Natenberg for systematic options strategies.

## Tier 3 — Crypto-native, ML, aggregators

### crypto_research

- **status:** `included_v1`
- **access_type:** `open`
- **corpus_destination:** `public`
- **what_to_acquire:** Research and writing from Paradigm, Multicoin Capital, and Galaxy Digital. Public blog posts covering crypto-native patterns: funding rate arbitrage, basis trades, MEV-adjacent strategies, perp-spot arbitrage, on-chain factor exposures. These patterns are largely absent from traditional finance literature.
- **place_at:** `sources/crypto/paradigm/`, `sources/crypto/multicoin/`, `sources/crypto/galaxy/`
- **format_expected:** html (saved as `.html` or converted to `.md`)
- **expected_volume:** ~25 strategies
- **notes:** Open public web. Focus on systematic / repeatable patterns, not one-off market commentary.

### hudson_thames

- **status:** `included_v1`
- **access_type:** `open`
- **corpus_destination:** `public`
- **what_to_acquire:** Hudson & Thames blog (hudsonthames.org) — ML-on-finance patterns, complementary to Lopez de Prado. Public web articles.
- **place_at:** `sources/hudson_thames/`
- **format_expected:** html
- **expected_volume:** ~10 strategies

### ssrn_papers

- **status:** `included_v1`
- **access_type:** `open` (mostly)
- **corpus_destination:** `public`
- **what_to_acquire:** Working papers from SSRN's Financial Economics Network covering cutting-edge / post-cutoff strategies. Most papers are free; a few require author permission. User provides a curated list of paper IDs or DOIs; AI search fetches the PDFs.
- **place_at:** `sources/ssrn/`
- **format_expected:** pdf
- **expected_volume:** ~30 strategies
- **notes:** Free SSRN papers are downloadable directly. If a paper requires auth, skip and log the reason.

## Concept-entry sources

### concept_seed_glossary

- **status:** `included_v1`
- **access_type:** `manual`
- **corpus_destination:** `public`
- **what_to_acquire:** A hand-prepared glossary of terms the corpus's concept entries should cover. User authors `sources/concepts/seed_glossary.md` listing terms: contango, backwardation, low dispersion, vol risk premium, basis, perpetual swap, funding rate, factor crowding, skew, term structure, etc.
- **place_at:** `sources/concepts/seed_glossary.md`
- **format_expected:** markdown (one term per line with a one-sentence description)
- **expected_volume:** ~30-50 concept entries
- **notes:** Seed glossary drives the concept drafter. AI search is not used here — user owns this list.

---

# Maintenance

## When sources release new content

Re-run this skill for that source id. Diff new artifacts vs. existing in `sources/<id>/` — the diff highlights what is new. New / updated artifacts then trigger re-drafting on affected corpus entries (see design doc, regression #13).

## When a source goes dark or licensing changes

1. Update the source entry's `status` field above (e.g., `deprecated`) with a date and reason in `notes`.
2. Existing derived corpus entries remain; flag them with a `source_status: deprecated` note in their frontmatter if licensing requires removal of derivative content.

## When a deferred source becomes ready

1. Move the entry from the "Deferred sources" section back into the active manifest with `status: included_v1`.
2. Run this skill for the source id.

---

# Deferred sources

The following sources are intentionally out of scope for v1. Not blocking; revisit in v2+ when the cost/benefit shifts.

### quantpedia (deferred to v2)

- **why deferred:** Paid annual subscription is expensive for this phase. TOS on derivative works also needs legal review.
- **what it would add:** Up to ~150 net-new strategies (after dedupe vs. OpenAP/HXZ) — broadest catalog across asset classes.
- **revisit when:** v1 ships and trace evidence shows the hub frequently encountering crypto / multi-asset / futures strategies that no other source covers. Or when budget allows.
- **target manifest entry (for v2 reactivation):** `status: included_v1`, `access_type: paid_api`, `corpus_destination: private`, `place_at: sources/quantpedia/`, `format_expected: json`.

---

# Open questions

- **Aggregator sources** (Quantocracy, Robot Wealth). Not in v1 manifest — treated as leads only. Revisit in v2 if specific patterns surface that no other source covers.
