---
name: review-draft
description: Review one OBaI knowledge-base draft corpus entry (strategy or concept) and execute a single verdict — promote it to the reviewed tree, drop it, mark it skipped, or hand back to the user for edit. Use this skill when the user invokes /review-draft with a draft path or `next` to pull the next pending entry from `corpus/_drafts/` (public) or `corpus_private/_drafts/` (private). The skill runs deterministic preflight checks via `scripts/review_draft.py`, presents the findings + rendered entry to the user, waits for the verdict, executes the move/delete/log step, updates `state/drafting_progress.csv`, and rebuilds `corpus.db` after a successful promote.
---

# Review Draft

Process exactly **one** draft per invocation. Surface deterministic findings, present the entry to the user, and act on a single verdict (`promote`, `drop`, `skip`, or `edit`).

## Paths

All paths resolve relative to the OBaI repo root, determined at invocation time:

```sh
REPO_ROOT="$(git rev-parse --show-toplevel)"
```

Abort with an explicit error if `git rev-parse --show-toplevel` fails or the basename is not `obai`.

Concrete anchors:

- **Public drafts:** `$REPO_ROOT/src/knowledge-base-server/corpus/_drafts/`
- **Private drafts:** `$REPO_ROOT/src/knowledge-base-server/corpus_private/_drafts/`
- **Public reviewed tree:** `$REPO_ROOT/src/knowledge-base-server/corpus/<category-root>/<category>/`
- **Private reviewed tree:** `$REPO_ROOT/src/knowledge-base-server/corpus_private/<category-root>/<category>/`
- **Progress CSV:** `$REPO_ROOT/src/knowledge-base-server/state/drafting_progress.csv`
- **Review helper:** `$REPO_ROOT/src/knowledge-base-server/scripts/review_draft.py`
- **Indexer:** `$REPO_ROOT/src/knowledge-base-server/scripts/build_index.py`

`<category-root>` is `strategies` or `concepts`. The path of a draft determines its target: `corpus/_drafts/strategies/momentum/foo.md` promotes to `corpus/strategies/momentum/foo.md`.

The skill never writes outside `corpus/`, `corpus_private/`, and `state/`. It never touches `sources/` or `scripts/`.

## Invocation forms

```
/review-draft <relative-or-absolute-path-to-draft.md>
/review-draft next
/review-draft next --private                              # restrict to corpus_private/_drafts/
/review-draft next --public                               # restrict to corpus/_drafts/
/review-draft <path> --target <category>                  # preflight as if promotion would re-file
```

`next` selects the alphabetically first draft under the chosen tree whose CSV status is empty or in `{drafted, skipped}`. Entries with terminal CSV status (`promoted`, `dropped`, `invalid`) are skipped automatically.

`--target <category>` runs the preflight as if the entry were going to land in a different category folder than the draft's current directory. Useful when an entry is filed in the wrong category. The override is also accepted on the `promote` verdict (Step 5).

## Procedure

### Step 1 — Resolve the target draft

- If a path was supplied, normalize to an absolute path and assert it is a `.md` file under one of the two `_drafts/` trees.
- If `next` was supplied, list `*.md` under the relevant `_drafts/` trees (filtered by `--public` / `--private` if given), sort alphabetically, skip any whose CSV status is `promoted | dropped | invalid`, and select the first remaining. If none remain, report "no pending drafts" and stop.

### Step 2 — Preflight

Run `uv run python $REPO_ROOT/src/knowledge-base-server/scripts/review_draft.py --draft <path> [--target-category <cat>]`. The helper returns JSON with: `frontmatter_valid`, `validation_error`, `entry_id`, `entry_type`, `category`, `canonical_name`, `corpus_destination`, `target_path`, `target_path_exists`, `target_category_override`, `duplicate_id_in_target`, `duplicate_canonical_name_owner`, `csv_status`, `concerns[]`, `recommendation`. The helper rejects category overrides that aren't in the design-doc enum and returns an `error` field instead of normal output — surface that error to the user and stop.

Recommendation grades:
- `promote-as-is` — passes validation, no concerns
- `review-recommended` — passes validation but has warnings worth a human look
- `review-required` — name conflict with an existing entry; user must decide
- `invalid` — validation error or duplicate id; cannot be promoted without fixing

### Step 3 — Present

Print a compact report to the user:

```
DRAFT       : <relative draft path>
ENTRY ID    : <id>            TYPE: <strategy|concept>            DEST: <public|private>
CATEGORY    : <category>      TARGET: <relative target path>      EXISTS: <yes|no>
VALIDATION  : <ok | FAIL: error>
DUPLICATES  : <none | id collides with promoted entry | canonical_name owned by <id>>
CSV STATUS  : <none | drafted | skipped | ...>
CONCERNS    :
  [warning] ...
  [info]    ...
RECOMMEND   : <promote-as-is | review-recommended | review-required | invalid>
```

Then render the full draft markdown so the user can read frontmatter + body in one screen. For very long entries (>200 lines), show the frontmatter + first 60 body lines + a `... (truncated, full file at <path>)` line.

### Step 4 — Wait for the user's verdict

The skill **must stop and ask** for a single command. Valid commands:

- `promote` — move to the reviewed tree. Blocked when recommendation is `invalid` unless the user passes `promote --force` (still does not bypass validator error; only bypasses duplicate-id by refusing the move).
- `drop` — delete the draft file. Use when the entry is low-quality and not worth fixing.
- `skip` — leave the draft in place, mark CSV status `skipped` so `next` moves past it.
- `edit` — open the draft path for the user. After they edit, they re-invoke `/review-draft <same-path>` to re-run preflight.

If the user replies with anything else (e.g. asks a follow-up question), answer it but do NOT silently take an action — wait for one of the four verdicts explicitly.

### Step 5 — Execute

**promote** (the most-used path):

The verdict accepts `promote --target <category>` to re-file the entry into a different category folder than the draft's directory. When `--target` is supplied:
- The skill MUST re-run preflight with `--target-category <cat>` to recompute `target_path` and refresh duplicate checks at the new location.
- Before moving, the skill MUST rewrite the draft file's frontmatter `category:` field to match `<cat>` so the entry stays consistent. Re-run `validate_frontmatter.py` after the rewrite; abort if validation now fails.

Procedure:
1. Block if validation failed: refuse and print the validator error. Hint: "edit the draft, then re-run /review-draft."
2. Block if `duplicate_id_in_target` is true: refuse. Print the colliding target path. Hint: "rename the id, then re-run /review-draft."
3. If `--target <cat>` was supplied: rewrite the draft's frontmatter `category:` field to `<cat>` and re-validate. Abort if the rewrite produces an invalid entry.
4. Ensure the target category directory exists (create if missing).
5. `mv` the draft to `target_path`. (Drafts live under `_drafts/` which is gitignored, so plain `mv`.)
6. Recompute the content SHA-256 of the new file path.
7. Append a row to `state/drafting_progress.csv` with columns `source_ref, draft_path, content_hash, drafted_at_iso, status, notes`. `source_ref` reuses any prior CSV row's value for the same file or falls back to `<entry_id>`; `status` is `promoted`; `notes` includes a short verdict line (e.g. `promoted via /review-draft 2026-05-15`) and the `--target` override if used (e.g. `recategorized momentum -> mean_reversion`).
8. Rebuild the index: `uv run python $REPO_ROOT/src/knowledge-base-server/scripts/build_index.py --include-private`. Print the resulting count line.
9. Confirm: `promoted <entry_id> to <target_path>; corpus.db now has N entries.` If a recategorization occurred, include `(recategorized <old_cat> -> <new_cat>)` in the line.

**drop**:
1. Confirm with the user *once* before deletion (single y/n) — irreversible.
2. Delete the draft file.
3. Append a CSV row with status `dropped`, notes carry a short reason if the user supplied one.
4. Print: `dropped <entry_id>; CSV updated.`

**skip**:
1. Append (or update) a CSV row with status `skipped`. No file change.
2. Print: `skipped <entry_id>; next /review-draft next will move past it.`

**edit**:
1. Print: `edit at: <absolute draft path>. When done, re-run /review-draft <path> to re-check.` Do NOT update the CSV — the entry is still pending.

### Step 6 — Stop

End the invocation cleanly. Do NOT loop into the next draft. The user controls cadence by re-invoking the skill (manually or via `/loop`).

## Hard constraints

- **One draft per invocation.** No batch processing inside this skill. Loop via `/loop` if the user wants pacing.
- **Never auto-promote.** Even with `recommendation: promote-as-is`, present + wait for the explicit `promote` verdict. The whole point is human-in-loop.
- **Never bypass the validator.** A failing schema means the entry never reaches `corpus.db`. The user must edit, not force.
- **Never touch `sources/` or `scripts/`.** Review is corpus-side only.
- **Never commit anything.** The skill stops at `mv` + CSV update + index rebuild. PR/commit decisions are the user's.
- **Never index the public corpus when `corpus_destination` is private.** The rebuild command is always `--include-private` because the maintainer's local build is the full index; the public-only build runs in CI, not in this skill.

## Reporting

After the verdict is executed, output a one-paragraph summary:

- **Draft**
- **Verdict** (`promoted | dropped | skipped | edit-handoff`)
- **New location** (for `promote`) or **affected file** (for the others)
- **Index counts** (for `promote` only)
- **Concerns surfaced** (count, by severity)
