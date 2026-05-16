"""Validate a single corpus markdown draft against the indexer's schema rules.

Exit 0 = valid. Exit 1 = invalid (error message on stderr).

Reuses parse_markdown + validate_entry from build_index.py so the drafter never
drifts from the indexer's accepted shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

from build_index import parse_markdown, validate_entry


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_frontmatter.py <path-to-draft.md>", file=sys.stderr)
        return 2
    path = Path(argv[1]).resolve()
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 2
    try:
        frontmatter, _body, sections = parse_markdown(path)
        validate_entry(path, frontmatter, sections)
    except ValueError as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
