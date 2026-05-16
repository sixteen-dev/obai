---
name: draft-corpus-entry
description: Draft one OBaI knowledge-base corpus entry (strategy or concept) from a source artifact. Use this skill when the user invokes /draft-corpus-entry with a source reference (e.g., `openap signal=Mom12m`, `paper id=jegadeesh_titman_1993`, `book source=ernie_chan chapter="momentum strategies"`, `concept name=contango`). Reads the source via a non-LLM parsing utility, loads the matching template, generates frontmatter + body, validates, writes the draft to corpus/_drafts/ (public) or corpus_private/_drafts/ (private) per the source's corpus_destination, and appends a row to state/drafting_progress.csv. Never writes outside _drafts/.
---

# Draft Corpus Entry

Draft a single strategy or concept entry for the OBaI knowledge base from a source artifact. This skill is one Claude Code session turn against a templated prompt — you read the source via a small Python helper, fill the template, validate, and write the result.

## Paths

All paths resolve relative to the OBaI repo root, determined at invocation time:

```sh
REPO_ROOT="$(git rev-parse --show-toplevel)"
```

Abort with an explicit error if `git rev-parse --show-toplevel` fails or the basename is not `obai`.

Concrete anchors:

- **Sources root:** `$REPO_ROOT/src/knowledge-base-server/sources/`
- **Public drafts:** `$REPO_ROOT/src/knowledge-base-server/corpus/_drafts/<category>/`
- **Private drafts:** `$REPO_ROOT/src/knowledge-base-server/corpus_private/_drafts/<category>/`
- **Progress CSV:** `$REPO_ROOT/src/knowledge-base-server/state/drafting_progress.csv`
- **Templates:** `$REPO_ROOT/.claude/skills/draft-corpus-entry/templates/`
- **Manifest:** `$REPO_ROOT/.claude/skills/fetch-corpus-source/SKILL.md` (source of truth for `corpus_destination`)

Never write outside `_drafts/`. Promotion to `corpus/<category>/` or `corpus_private/<category>/` happens via PR review, not this skill.

## Invocation

```
/draft-corpus-entry openap signal=Mom12m
/draft-corpus-entry paper id=jegadeesh_titman_1993
/draft-corpus-entry book source=ernie_chan chapter="momentum strategies"
/draft-corpus-entry concept name=contango
/draft-corpus-entry openap next-from-queue
```

For batch runs, wrap in `/loop`:

```
/loop 30s /draft-corpus-entry openap next-from-queue
```

## Procedure

1. **Parse the source reference.** Determine the source id (e.g. `openap`, `aqr_research`, `ernie_chan`) and the specific selector (signal id, paper id, book + chapter, concept name).
2. **Look up `corpus_destination`** in the fetch skill's manifest. Public → write to `corpus/_drafts/<category>/`. Private → write to `corpus_private/_drafts/<category>/`.
3. **Check `state/drafting_progress.csv`** for a row matching `(source_ref, content_hash)`. If present and the user did not pass `force=true`, skip and report the existing draft path.
4. **Extract structured context** by calling the appropriate Python parser via Bash:
   - `scripts/parse_openap_row.py` — one signal from `sources/openap/`
   - `scripts/parse_paper_excerpt.py` — relevant section from a PDF
   - `scripts/parse_book_chapter.py` — one chapter from a book PDF
   - `scripts/parse_concept_seed.py` — one entry from the concept glossary
   These return a structured dict; render its fields into the template's placeholders.
5. **Load the matching template** from `.claude/skills/draft-corpus-entry/templates/`:
   - `draft_strategy.md` for strategies (every invocation)
   - `draft_concept.md` for concepts
   - Always inject `draft_engine_reference.md` content into the strategy template's `{{engine_reference}}` placeholder. Concepts do not use the engine reference.
6. **Generate the markdown** following the template's rules. Output the file content only — no commentary, no preamble.
7. **Validate frontmatter** by piping the generated content through `scripts/validate_frontmatter.py`. On failure, write the draft to `_drafts/_invalid/<id>.md` and log `status=invalid` in the progress CSV.
8. **Write the draft** to the resolved `_drafts/<category>/<id>.md` path. Create category directories as needed.
9. **Append a progress row** to `state/drafting_progress.csv` with columns: `source_ref, draft_path, content_hash, drafted_at_iso, status, notes`. Create the file with a header row if it doesn't exist.

## Hard constraints

- **Never fabricate** papers, aliases, or failure modes. If a required field cannot be filled from the source, leave it blank — the validator will flag it.
- **Never write to `corpus/<category>/` or `corpus_private/<category>/` directly.** Only `_drafts/` paths.
- **Never consult `corpus.db` at draft time.** This skill is write-only against the corpus tree; it does not read existing entries.
- **Never call the runtime `knowledge_base_agent`.** Strict separation from the lookup agent.
- **`engine_fit` is derived from `draft_engine_reference.md`, not training data.** If the engine reference doesn't cover the mechanic, default to `reference_only` and put the reason in `approximation_notes`.
- **No paywalled content extraction.** If a parser surfaces text from a paywalled artifact (e.g. a book PDF the user provided), draft normally — the user owns the legal posture for `manual_purchase` sources.

## Reporting

After completing (or skipping), output a one-paragraph summary:

- **Source ref**
- **Status**: `drafted | skipped | invalid | failed`
- **Draft path**
- **Notes** (validation warnings, missing fields, content_hash)
