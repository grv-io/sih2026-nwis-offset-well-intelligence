"""Split page text into LLM-extraction-sized chunks with provenance.

DDRs are narrative logs, usually one entry per report day (sometimes several
per page for multi-day summaries). We first try to split on date-header-like
lines (`11-Mar-2019`, `2019-03-11`, `Date: 11/03/19`, ...). If no such headers
are found, fall back to splitting on blank-line paragraph breaks. Either way,
oversized sections are capped at MAX_CHARS with OVERLAP-character overlap so
no sentence context is lost across a chunk boundary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from nwis.ingest.extract_text import PageText

MAX_CHARS = 1200
OVERLAP = 100

_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
_DATE_HEADER_RE = re.compile(
    rf"^\s*(?:date\s*[:\-]?\s*)?"
    rf"(?:\d{{1,2}}[-/](?:{_MONTHS}|\d{{1,2}})[-/]\d{{2,4}}"
    rf"|\d{{4}}-\d{{2}}-\d{{2}}"
    rf"|\d{{1,2}}[-/]\d{{1,2}}[-/]\d{{2,4}})\b",
    re.IGNORECASE,
)


@dataclass
class Chunk:
    text: str
    page_ref: str


def _split_on_date_headers(text: str) -> list[str]:
    lines = text.splitlines()
    sections: list[list[str]] = []
    for line in lines:
        if _DATE_HEADER_RE.match(line):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            sections.append([line])
    return ["\n".join(s).strip() for s in sections if "\n".join(s).strip()]


def _split_on_blank_lines(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n+", text)
    return [b.strip() for b in blocks if b.strip()]


def _cap_with_overlap(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        out.append(text[start:end])
        if end >= n:
            break
        start = end - overlap
    return out


def chunk_page(page: PageText, pdf_name: str) -> list[Chunk]:
    """Chunk a single page's text. `pdf_name` is the source filename (with extension)
    used to build `page_ref = f"{pdf_name}#p{page.page_no}"`.
    """
    text = page.text or ""
    if not text.strip():
        return []

    sections = _split_on_date_headers(text)
    n_headers = sum(1 for s in text.splitlines() if _DATE_HEADER_RE.match(s))
    if n_headers < 2:
        # Not enough date headers to trust that split — use paragraph breaks instead.
        sections = _split_on_blank_lines(text)
    if not sections:
        sections = [text.strip()]

    page_ref = f"{pdf_name}#p{page.page_no}"
    chunks: list[Chunk] = []
    for sec in sections:
        for piece in _cap_with_overlap(sec):
            piece = piece.strip()
            if piece:
                chunks.append(Chunk(text=piece, page_ref=page_ref))
    return chunks


def chunk_pages(pages: list[PageText], pdf_name: str) -> list[Chunk]:
    out: list[Chunk] = []
    for page in pages:
        out.extend(chunk_page(page, pdf_name))
    return out
