"""Extract one chapter from a book PDF for corpus drafting.

Usage:
    parse_book_chapter.py --source <id> --chapter <N>
    parse_book_chapter.py --source <id> --chapter <"title-fragment">
    parse_book_chapter.py --source <id> --pages <START>:<END>

Source ids are registered in BOOK_REGISTRY. Output is JSON on stdout:
    {
      "book_id": "...", "author": "...", "title": "...", "year": ...,
      "chapter_number": N, "chapter_title": "...",
      "pdf_pages": [start, end], "word_count": N, "text": "...",
      "citation": "..."
    }
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources"
TOC_HEADER_MIN_PDF_PAGE = 15  # chapter content starts after front-matter TOC


@dataclass(frozen=True)
class BookEntry:
    source_id: str
    pdf_path: Path
    author: str
    title: str
    edition: str
    year: int
    publisher: str
    chapters: dict[int, str]  # chapter number -> printed title


BOOK_REGISTRY: dict[str, BookEntry] = {
    "sinclair": BookEntry(
        source_id="sinclair",
        pdf_path=SOURCES / "sinclair" / "volatility_trading_2e_2013.pdf",
        author="Euan Sinclair",
        title="Volatility Trading",
        edition="2nd ed.",
        year=2013,
        publisher="Wiley",
        chapters={
            1: "Option Pricing",
            2: "Volatility Measurement",
            3: "Stylized Facts about Returns and Volatility",
            4: "Volatility Forecasting",
            5: "Implied Volatility Dynamics",
            6: "Hedging",
            7: "Distribution of Hedged Option Positions",
            8: "Money Management",
            9: "Trade Evaluation",
            10: "Psychology",
            11: "Generating Returns through Volatility",
            12: "The VIX",
            13: "Leveraged ETFs",
            14: "Life Cycle of a Trade",
            15: "Conclusion",
        },
    ),
    "natenberg": BookEntry(
        source_id="natenberg",
        pdf_path=SOURCES / "natenberg" / "option_volatility_and_pricing_2e_2014.pdf",
        author="Sheldon Natenberg",
        title="Option Volatility and Pricing",
        edition="2nd ed.",
        year=2014,
        publisher="McGraw-Hill",
        chapters={},  # filled when needed
    ),
    "lopez_de_prado": BookEntry(
        source_id="lopez_de_prado",
        pdf_path=SOURCES / "lopez_de_prado" / "ml_for_asset_managers_2020_SAMPLE.pdf",
        author="Marcos M. López de Prado",
        title="Machine Learning for Asset Managers",
        edition="1st ed.",
        year=2020,
        publisher="Cambridge University Press",
        chapters={},  # filled when full PDF arrives
    ),
}


def pdftotext_layout(pdf: Path, first: int | None = None, last: int | None = None) -> str:
    """Run pdftotext with layout preservation, page-separated by form feeds."""
    cmd = ["pdftotext", "-layout"]
    if first is not None:
        cmd += ["-f", str(first)]
    if last is not None:
        cmd += ["-l", str(last)]
    cmd += [str(pdf), "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {result.stderr.strip()}")
    return result.stdout


def find_chapter_pdf_pages(text: str, chapter_num: int) -> tuple[int, int]:
    """Locate PDF pages where chapter N starts and ends.

    Strategy: pages are separated by form-feed \\f. The TOC sits in the first
    few pages of the PDF, so chapter content begins at the FIRST occurrence of
    'CHAPTER N' on a PDF page numbered >= TOC_HEADER_MIN_PDF_PAGE.
    """
    pages = text.split("\f")
    starts: dict[int, int] = {}
    header_re = re.compile(rf"^\s*CHAPTER\s+{chapter_num}\b", re.MULTILINE)
    next_header_re = re.compile(rf"^\s*CHAPTER\s+{chapter_num + 1}\b", re.MULTILINE)

    for pdf_page_index, page_text in enumerate(pages, start=1):
        if pdf_page_index < TOC_HEADER_MIN_PDF_PAGE:
            continue
        if header_re.search(page_text):
            starts.setdefault(chapter_num, pdf_page_index)

    if chapter_num not in starts:
        raise ValueError(f"chapter {chapter_num} header not found past page {TOC_HEADER_MIN_PDF_PAGE}")

    end_page = len(pages)
    for pdf_page_index in range(starts[chapter_num] + 1, len(pages) + 1):
        if next_header_re.search(pages[pdf_page_index - 1]):
            end_page = pdf_page_index - 1
            break
    return starts[chapter_num], end_page


def extract_chapter(book: BookEntry, chapter_num: int) -> tuple[int, int, str]:
    """Extract chapter text + the PDF page range it spans."""
    if chapter_num not in book.chapters and book.chapters:
        valid = sorted(book.chapters)
        raise ValueError(f"chapter {chapter_num} not in {book.source_id} registry; valid: {valid}")
    full_text = pdftotext_layout(book.pdf_path)
    start, end = find_chapter_pdf_pages(full_text, chapter_num)
    chapter_text = pdftotext_layout(book.pdf_path, first=start, last=end)
    return start, end, chapter_text


def extract_pages(book: BookEntry, first: int, last: int) -> str:
    return pdftotext_layout(book.pdf_path, first=first, last=last)


def build_citation(book: BookEntry) -> str:
    return f"{book.author} ({book.year}). {book.title} ({book.edition}). {book.publisher}."


def assemble_result(book: BookEntry, chapter_num: int | None, start: int, end: int, text: str) -> dict[str, object]:
    word_count = len(text.split())
    chapter_title = book.chapters.get(chapter_num, "") if chapter_num else ""
    return {
        "book_id": f"{book.source_id}_{book.title.lower().replace(' ', '_')}_{book.year}",
        "source_id": book.source_id,
        "author": book.author,
        "title": book.title,
        "edition": book.edition,
        "year": book.year,
        "publisher": book.publisher,
        "chapter_number": chapter_num,
        "chapter_title": chapter_title,
        "pdf_pages": [start, end],
        "word_count": word_count,
        "text": text,
        "citation": build_citation(book),
    }


def parse_pages_arg(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("--pages must be START:END")
    first, last = int(parts[0]), int(parts[1])
    if first <= 0 or last < first:
        raise argparse.ArgumentTypeError("invalid page range")
    return first, last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, choices=sorted(BOOK_REGISTRY))
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--chapter", type=int)
    selector.add_argument("--pages", type=parse_pages_arg)
    args = parser.parse_args()

    book = BOOK_REGISTRY[args.source]
    if not book.pdf_path.is_file():
        print(f"error: missing PDF {book.pdf_path}", file=sys.stderr)
        return 2

    if args.chapter is not None:
        start, end, text = extract_chapter(book, args.chapter)
        result = assemble_result(book, args.chapter, start, end, text)
    else:
        first, last = args.pages
        text = extract_pages(book, first, last)
        result = assemble_result(book, None, first, last, text)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
