"""Weak/regex ground-truth labels for the Volve DDR `.txt` docs, pre-registered
BEFORE any extraction run against them (DrillScribe's `transfer_bsee.py` pattern,
research/07 §2.6; docs/IMPLEMENTATION_PLAN.md §6 A10/A13).

    python -m nwis.external.volve.labels [--docs-dir data/external/volve/docs]

Frozen regex set, one per `nwis.schema.EventType` member we can plausibly detect from
short narrative lines without North-Sea-specific domain vocabulary:

    mud_loss              losses|lost circulation|LCM
    stuck_pipe            stuck|overpull|jar
    kick                   kick|influx|gain
    cementing_issue        cement (squeeze|failure|channel)
    fishing_operation      fish(ing)?|junk
    wellbore_instability   tight hole|caving|pack.?off

These were chosen and frozen in this exact form during this build (26 Sep 2026),
before running the LLM extractor over the same docs, so the eval numbers in
`models/volve_metrics.json` cannot have been reverse-engineered from what the
extractor happened to output.

LIMITATIONS -- read before citing these numbers anywhere:
- These are keyword proxies, not human-adjudicated judgements. Same caveat DrillScribe
  states about its own `NPT_PHRASES` regex: recall is biased down by design (a strict
  regex misses paraphrased events), never flattered -- but a regex hit can still be a
  lexical false positive in drilling jargon (e.g. a line that explicitly says a kick
  was NOT observed still matches `\bkick\b`).
- One label per (line, matched event_type); a single real event narrated across two
  consecutive activity lines can be double-counted, or under-counted if the trigger
  word falls in a line this pass doesn't see as a boundary.
- `depth_md_m` falls back to the report day's END depth (the "Depth: X -> Y m MD"
  header line, i.e. `statusInfo[0].md` from the parquet) whenever the matched line
  itself carries no explicit depth number -- this is frequently WRONG for an event
  that happened earlier in that day's drilling interval, not at the day's final depth.
  Treat weak-truth depth as approximate, +/- one day's footage (highly variable: a few
  metres on a bad day, 100+ m on a fast one).
- No formation label is ever assigned -- Volve's real North Sea formations are not
  members of the frozen, Upper-Assam-only `nwis.schema.FORMATIONS` (see
  `nwis/external/volve/wells.py` docstring) -- so formation_accuracy in the eval output
  will read `n/a` for Volve, which is expected, not a bug.
- This is an evaluation convenience, not a benchmark. Precision/recall against these
  labels tells you whether the LLM extractor's keyword-level behaviour agrees with a
  cheap regex proxy, not whether it correctly understood the drilling engineering.
  Report it as such in the deck -- do not present this as a validated F1 score the way
  `data/synthetic/events_truth.json` (hand-designed ground truth) can be.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path
from typing import Optional

from nwis.config import settings

VOLVE_DIR = settings.external_dir / "volve"
DEFAULT_DOCS_DIR = VOLVE_DIR / "docs"

# Frozen 26 Sep 2026. Extend only via a new dated docs/PROJECT_LOG.md entry, never silently.
LABEL_PATTERNS: dict[str, re.Pattern] = {
    "mud_loss": re.compile(r"\blosses?\b|lost circulation|\bLCM\b", re.IGNORECASE),
    "stuck_pipe": re.compile(r"\bstuck\b|\boverpull(?:ed|ing)?\b|\bjar(?:red|ring)?\b", re.IGNORECASE),
    "kick": re.compile(r"\bkick\b|\binflux\b|\bgain\b", re.IGNORECASE),
    "cementing_issue": re.compile(r"\bcement\w*\s+(?:squeeze|failure|channel\w*)", re.IGNORECASE),
    "fishing_operation": re.compile(r"\bfish(?:ing)?\b|\bjunk\b", re.IGNORECASE),
    "wellbore_instability": re.compile(r"tight hole|\bcaving\b|pack.?off", re.IGNORECASE),
}

_DEPTH_RE = re.compile(r"(\d{2,5}(?:[.,]\d+)?)\s*m(?:d)?\b", re.IGNORECASE)

_WELL_RE = re.compile(r"^Well:\s*(\S+)", re.MULTILINE)
_DATE_RE = re.compile(r"Report Date:\s*(\d{4}-\d{2}-\d{2})")
_DEPTH_LINE_RE = re.compile(r"Depth:\s*([0-9?.]+)\s*->\s*([0-9?.]+)\s*m MD", re.IGNORECASE)


# --------------------------------------------------------------------------- #
def label_line(text: str) -> list[str]:
    """Every event_type whose frozen regex matches this line (usually 0, sometimes 1;
    rarely more than 1 -- e.g. a line describing both a loss and a stuck-pipe remedy)."""
    return [event_type for event_type, pattern in LABEL_PATTERNS.items() if pattern.search(text)]


def _explicit_depth(text: str) -> Optional[float]:
    m = _DEPTH_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_ddr_txt(path: Path) -> dict:
    """Pull well_id / report_date / end-of-day depth / narrative lines back out of a
    `.txt` doc in the shape `prepare_docs.render_ddr_txt` writes."""
    text = path.read_text(encoding="utf-8")

    well_m = _WELL_RE.search(text)
    date_m = _DATE_RE.search(text)
    depth_m = _DEPTH_LINE_RE.search(text)

    well_id = well_m.group(1) if well_m else None
    report_date = date_m.group(1) if date_m else None
    end_md: Optional[float] = None
    if depth_m:
        try:
            end_md = float(depth_m.group(2))
        except ValueError:
            end_md = None

    lines: list[str] = []
    in_ops = False
    for raw_line in text.splitlines():
        if "OPERATIONS SUMMARY" in raw_line:
            in_ops = True
            continue
        if in_ops:
            stripped = raw_line.strip()
            if stripped.startswith("----"):
                break
            if stripped:
                lines.append(stripped)

    return {"well_id": well_id, "report_date": report_date, "end_md_m": end_md, "lines": lines}


def build_weak_truth(docs_dir: Path = DEFAULT_DOCS_DIR) -> list[dict]:
    """One weak-truth `DrillingEvent`-shaped dict per (doc, line, matched event_type),
    in the same shape as `data/synthetic/events_truth.json` (a plain JSON list, read
    directly by `nwis.ingest.eval.load_truth` -- not validated through
    `nwis.schema.DrillingEvent`, so North-Sea-incompatible fields like `formation=None`
    are fine here)."""
    out: list[dict] = []
    for txt_path in sorted(docs_dir.rglob("DDR_*.txt")):
        parsed = parse_ddr_txt(txt_path)
        well_id, report_date = parsed["well_id"], parsed["report_date"]
        if not well_id or not report_date:
            continue

        for i, line in enumerate(parsed["lines"]):
            hits = label_line(line)
            if not hits:
                continue
            depth = _explicit_depth(line)
            if depth is None:
                depth = parsed["end_md_m"]

            for event_type in hits:
                out.append({
                    "event_id": str(uuid.uuid5(
                        uuid.NAMESPACE_URL, f"{well_id}|{report_date}|{event_type}|{i}|{txt_path.name}"
                    )),
                    "well_id": well_id,
                    "source_document_id": None,
                    "source_page_ref": f"DDR_{report_date}.txt#p1",
                    "report_date": report_date,
                    "depth_md_m": depth,
                    "depth_tvd_m": None,
                    "formation": None,
                    "event_type": event_type,
                    "severity": "medium",
                    "free_text": line,
                    "extraction_confidence": 1.0,
                    "extraction_method": "weak_regex_label",
                    "reviewed_by_human": False,
                })
    return out


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build weak-label ground truth for Volve DDR docs.")
    parser.add_argument("--docs-dir", type=str, default=str(DEFAULT_DOCS_DIR))
    parser.add_argument("--out", type=str, default=str(VOLVE_DIR / "weak_truth.json"))
    args = parser.parse_args(argv)

    docs_dir = Path(args.docs_dir)
    if not docs_dir.exists():
        raise SystemExit(f"docs dir not found: {docs_dir} -- run prepare_docs.py first")

    truth = build_weak_truth(docs_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")

    by_type: dict[str, int] = {}
    for t in truth:
        by_type[t["event_type"]] = by_type.get(t["event_type"], 0) + 1
    print(f"Wrote {len(truth)} weak-label events to {out_path}")
    for et, n in sorted(by_type.items()):
        print(f"  {et:<22}{n}")


if __name__ == "__main__":
    main()
