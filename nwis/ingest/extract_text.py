"""PDF/text -> per-page text extraction.

Strategy: pdfplumber first (born-digital PDFs are free). If a page's text layer
yields fewer than OCR_MIN_CHARS characters, treat it as scanned and fall back to
rapidocr-onnxruntime on a rendered image of that page. OCR results are cached to
`<pdf_stem>.ocr.json` next to the source PDF so re-running ingest doesn't re-OCR.

`.txt` files are accepted directly and returned as a single page (method="txt").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Union

OCR_MIN_CHARS = 20


@dataclass
class PageText:
    page_no: int
    text: str
    method: str  # "pdfplumber" | "ocr" | "txt"


# --------------------------------------------------------------------------- #
# OCR engine (lazy singleton — rapidocr-onnxruntime loads ONNX models on first use)
# --------------------------------------------------------------------------- #
_ocr_engine = None


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR  # local import: heavy, only needed for OCR path
        _ocr_engine = RapidOCR()
    return _ocr_engine


def _ocr_result_to_text(result) -> str:
    """Join RapidOCR boxes top-to-bottom, left-to-right into reading-order text."""
    if not result:
        return ""
    items: list[tuple[float, float, str]] = []
    for box, text, _score in result:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        items.append((sum(ys) / len(ys), min(xs), text))
    items.sort(key=lambda t: (t[0], t[1]))

    # Group into lines: an item joins the most recent line if its y-center is
    # within `tol` of that line's running mean y (handles slight skew/jitter).
    tol = 12.0
    lines: list[list[tuple[float, float, str]]] = []
    line_means: list[float] = []
    for y, x, text in items:
        if lines and abs(line_means[-1] - y) <= tol:
            lines[-1].append((y, x, text))
            n = len(lines[-1])
            line_means[-1] = line_means[-1] + (y - line_means[-1]) / n
        else:
            lines.append([(y, x, text)])
            line_means.append(y)

    out_lines = []
    for line in lines:
        line.sort(key=lambda t: t[1])
        out_lines.append(" ".join(t[2] for t in line))
    return "\n".join(out_lines)


def _ocr_page_image(page) -> str:
    import numpy as np

    engine = _get_ocr_engine()
    pil_img = page.to_image(resolution=200).original
    arr = np.array(pil_img.convert("RGB"))
    result, _elapse = engine(arr)
    return _ocr_result_to_text(result)


# --------------------------------------------------------------------------- #
# OCR cache
# --------------------------------------------------------------------------- #
def _cache_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".ocr.json")


def _load_cache(pdf_path: Path) -> dict:
    p = _cache_path(pdf_path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt cache, just re-OCR
            return {}
    return {}


def _save_cache(pdf_path: Path, cache: dict) -> None:
    _cache_path(pdf_path).write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
def pdf_to_pages(path: Union[str, Path]) -> list[PageText]:
    p = Path(path)

    if p.suffix.lower() == ".txt":
        text = p.read_text(encoding="utf-8", errors="replace")
        return [PageText(page_no=1, text=text, method="txt")]

    import pdfplumber

    pages: list[PageText] = []
    cache: dict | None = None
    cache_dirty = False

    with pdfplumber.open(str(p)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if len(text.strip()) >= OCR_MIN_CHARS:
                pages.append(PageText(page_no=i, text=text, method="pdfplumber"))
                continue

            if cache is None:
                cache = _load_cache(p)
            key = str(i)
            if key in cache:
                pages.append(PageText(page_no=i, text=cache[key]["text"], method=cache[key].get("method", "ocr")))
                continue

            ocr_text = _ocr_page_image(page)
            cache[key] = {"text": ocr_text, "method": "ocr"}
            cache_dirty = True
            pages.append(PageText(page_no=i, text=ocr_text, method="ocr"))

    if cache_dirty and cache is not None:
        _save_cache(p, cache)

    return pages
