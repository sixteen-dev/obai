"""Run deterministic preflight checks on a single draft corpus entry.

Outputs JSON to stdout. Never modifies files; never prompts. Designed to be
called by the /review-draft skill and consumed programmatically.

Concerns are graded:
    error    -> blocks promotion (validator failure, duplicate id, etc.)
    warning  -> human should look (generic failure modes, short when_to_*, etc.)
    info     -> minor cosmetic notes (missing optional fields)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CORPUS = ROOT / "corpus"
PRIVATE_CORPUS = ROOT / "corpus_private"
PROGRESS_CSV = ROOT / "state" / "drafting_progress.csv"

VALID_STRATEGY_CATEGORIES = {
    "momentum", "mean_reversion", "vol", "options_structures", "carry",
    "quality", "size", "low_volatility", "crypto_native", "microstructure",
    "event_driven", "other",
}
VALID_CONCEPT_CATEGORIES = {"regimes", "instruments", "factors", "mechanics"}

# Reuse the indexer's validation so we never drift from build_index.py.
sys.path.insert(0, str(ROOT / "scripts"))
from build_index import parse_markdown, validate_entry  # noqa: E402


def derive_destination(draft_path: Path, target_category: str | None = None) -> tuple[str, Path]:
    """Map a draft path to (destination_label, target_review_path).

    If target_category is supplied, the second path segment (the category
    folder, e.g. `momentum`) is replaced before resolving the target path.
    The remaining tail (the filename) is preserved.
    """
    parts = draft_path.parts
    if "corpus_private" in parts:
        idx = parts.index("corpus_private")
        if parts[idx + 1] != "_drafts":
            raise ValueError(f"unexpected layout: {draft_path}")
        destination = "private"
        base = PRIVATE_CORPUS
    elif "corpus" in parts:
        idx = parts.index("corpus")
        if parts[idx + 1] != "_drafts":
            raise ValueError(f"unexpected layout: {draft_path}")
        destination = "public"
        base = PUBLIC_CORPUS
    else:
        raise ValueError(f"draft path not under corpus/_drafts or corpus_private/_drafts: {draft_path}")

    tail = list(parts[idx + 2:])  # ["strategies"|"concepts", "<category>", "<id>.md"]
    if target_category is not None:
        if len(tail) < 3:
            raise ValueError(f"draft layout too shallow for category override: {draft_path}")
        entry_type_root = tail[0]
        if entry_type_root == "strategies" and target_category not in VALID_STRATEGY_CATEGORIES:
            raise ValueError(f"target_category {target_category!r} not in strategy enum: {sorted(VALID_STRATEGY_CATEGORIES)}")
        if entry_type_root == "concepts" and target_category not in VALID_CONCEPT_CATEGORIES:
            raise ValueError(f"target_category {target_category!r} not in concept enum: {sorted(VALID_CONCEPT_CATEGORIES)}")
        tail[1] = target_category
    return destination, base.joinpath(*tail)


def load_csv_status(draft_path: Path) -> dict[str, str] | None:
    """Return the most recent CSV row matching this draft, or None."""
    if not PROGRESS_CSV.is_file():
        return None
    matches: list[dict[str, str]] = []
    target = str(draft_path)
    with PROGRESS_CSV.open() as f:
        for row in csv.DictReader(f):
            if row["draft_path"] == target:
                matches.append(row)
    return matches[-1] if matches else None


def collect_existing_ids_and_names() -> tuple[set[str], dict[str, str]]:
    """Scan public + private reviewed trees for already-promoted entries."""
    ids: set[str] = set()
    names: dict[str, str] = {}
    for root in (PUBLIC_CORPUS, PRIVATE_CORPUS):
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            if "_drafts" in path.parts:
                continue
            try:
                fm, _body, _sections = parse_markdown(path)
            except (ValueError, OSError):
                continue
            entry_id = str(fm.get("id", "")).strip()
            name = str(fm.get("canonical_name", "")).strip()
            if entry_id:
                ids.add(entry_id)
            if name:
                names[name.casefold()] = entry_id
    return ids, names


def heuristic_concerns(fm: dict, body: str) -> list[dict[str, str]]:
    """Surface common quality issues that the schema validator does not catch."""
    concerns: list[dict[str, str]] = []
    entry_type = fm.get("entry_type", "")

    def add(severity: str, message: str) -> None:
        concerns.append({"severity": severity, "message": message})

    # Snake_case id check
    entry_id = str(fm.get("id", ""))
    if entry_id and not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", entry_id):
        add("warning", f"id {entry_id!r} is not snake_case")

    # Title case canonical_name (weak heuristic)
    canonical = str(fm.get("canonical_name", ""))
    if canonical and canonical == canonical.lower():
        add("info", "canonical_name is fully lowercase; consider Title Case")

    # Strategy-only checks
    if entry_type == "strategy":
        # engine_fit vs approximation_notes consistency
        engine_fit = str(fm.get("engine_fit", ""))
        approx = str(fm.get("approximation_notes") or "").strip()
        if engine_fit == "native" and approx:
            add("warning", "engine_fit=native but approximation_notes is non-empty (likely contradiction)")

        # Failure-mode specificity
        failures = fm.get("known_failure_modes") or []
        if isinstance(failures, list):
            if len(failures) < 2:
                add("warning", f"known_failure_modes has only {len(failures)} entries (recommend 2-5 specific items)")
            for i, item in enumerate(failures):
                if isinstance(item, str) and len(item) < 40:
                    add("info", f"known_failure_modes[{i}] is very short ({len(item)} chars); may be generic")

        # when_to_consider / when_to_avoid specificity
        for field in ("when_to_consider", "when_to_avoid"):
            val = str(fm.get(field) or "").strip()
            if 0 < len(val) < 40:
                add("info", f"{field} is short ({len(val)} chars); may be generic")
        wtc = str(fm.get("when_to_consider") or "").strip()
        wta = str(fm.get("when_to_avoid") or "").strip()
        if wtc and wtc == wta:
            add("warning", "when_to_consider equals when_to_avoid (likely placeholder)")

        # seminal_papers presence
        if not fm.get("seminal_papers"):
            add("info", "no seminal_papers cited")

        # Body length
        word_count = len(body.split())
        if word_count < 120:
            add("warning", f"body is short ({word_count} words); thesis + signal intuition likely thin")

    # Concept-only checks
    if entry_type == "concept":
        definition = str(fm.get("definition") or "").strip()
        if 0 < len(definition) < 40:
            add("warning", f"definition is short ({len(definition)} chars)")
        when_matters = str(fm.get("when_it_matters") or "").strip()
        if 0 < len(when_matters) < 40:
            add("warning", f"when_it_matters is short ({len(when_matters)} chars)")

    return concerns


def render_recommendation(validation_error: str | None, dup_id: bool, dup_name: bool, concerns: list[dict[str, str]]) -> str:
    if validation_error or dup_id:
        return "invalid"
    if dup_name:
        return "review-required"
    if any(c["severity"] == "warning" for c in concerns):
        return "review-recommended"
    return "promote-as-is"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument(
        "--target-category",
        default=None,
        help="Override the category folder when computing target_path "
        "(e.g. promote a draft filed under momentum into mean_reversion).",
    )
    args = parser.parse_args()

    draft = args.draft.resolve()
    result: dict[str, object] = {"draft_path": str(draft.relative_to(ROOT.parent)) if ROOT.parent in draft.parents else str(draft)}

    if not draft.is_file():
        result["draft_exists"] = False
        result["error"] = "draft file not found"
        json.dump(result, sys.stdout, indent=2)
        return 1
    result["draft_exists"] = True

    try:
        destination, target_path = derive_destination(draft, args.target_category)
        result["corpus_destination"] = destination
        result["target_path"] = str(target_path.relative_to(ROOT.parent)) if ROOT.parent in target_path.parents else str(target_path)
        result["target_category_override"] = args.target_category
    except ValueError as exc:
        result["error"] = str(exc)
        json.dump(result, sys.stdout, indent=2)
        return 1

    try:
        fm, body, sections = parse_markdown(draft)
        validate_entry(draft, fm, sections)
        result["frontmatter_valid"] = True
        result["validation_error"] = None
    except ValueError as exc:
        result["frontmatter_valid"] = False
        result["validation_error"] = str(exc)
        fm, body = {}, ""

    result["entry_id"] = fm.get("id", "")
    result["entry_type"] = fm.get("entry_type", "")
    result["category"] = fm.get("category", "")
    result["canonical_name"] = fm.get("canonical_name", "")

    ids, names = collect_existing_ids_and_names()
    dup_id = bool(result["entry_id"]) and result["entry_id"] in ids
    dup_name_id = names.get(str(result["canonical_name"]).casefold()) if result["canonical_name"] else None
    dup_name = bool(dup_name_id) and dup_name_id != result["entry_id"]
    result["duplicate_id_in_target"] = dup_id
    result["duplicate_canonical_name_owner"] = dup_name_id if dup_name else None
    result["target_path_exists"] = target_path.is_file()

    csv_row = load_csv_status(draft)
    result["csv_status"] = csv_row["status"] if csv_row else None

    result["concerns"] = heuristic_concerns(fm, body) if fm else []
    result["recommendation"] = render_recommendation(
        result.get("validation_error"),
        dup_id,
        dup_name,
        result["concerns"],
    )

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
