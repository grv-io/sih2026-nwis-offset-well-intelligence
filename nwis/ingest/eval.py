"""Evaluate extracted events against ground truth.

    python -m nwis.ingest.eval --truth data/synthetic/events_truth.json [--db PATH]

Truth file: a JSON list of objects with (at least) `well_id`, `event_type`,
`depth_md_m` (optional), `formation` (optional), `report_date` (optional, ISO
date) and/or `source_document_id` (optional) — the same shape as
`DrillingEvent`/the schema in research/05_technical_design.md §1. Matching is
greedy, per (well_id, event_type) group: a truth event and an extracted event
match if |depth diff| <= DEPTH_TOL_M (when both have a depth) AND they share a
source_document_id or a report_date (when both sides have that field).

Writes `models/extraction_metrics.json` and prints a summary table. This number
goes straight into the SIH deck, so it is computed honestly: no depth tolerance
games. If truth has zero rows for a well, precision counts against you as FP
just like recall counts as FN — no free passes.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Optional

from nwis import db
from nwis.config import settings
from nwis.ingest.normalise import canonical_formation

DEPTH_TOL_M = 25.0


# --------------------------------------------------------------------------- #
class TruthEvent:
    __slots__ = ("well_id", "event_type", "depth_md_m", "formation", "report_date", "source_document_id")

    def __init__(self, d: dict):
        self.well_id = d.get("well_id")
        self.event_type = d.get("event_type")
        self.depth_md_m = d.get("depth_md_m")
        self.formation = d.get("formation")
        rd = d.get("report_date")
        self.report_date = date.fromisoformat(rd) if isinstance(rd, str) else rd
        self.source_document_id = d.get("source_document_id")


class ExtractedEvent:
    __slots__ = ("well_id", "event_type", "depth_md_m", "formation", "report_date", "source_document_id", "matched")

    def __init__(self, row):
        self.well_id = row.well_id
        self.event_type = row.event_type
        self.depth_md_m = row.depth_md_m
        self.formation = row.formation
        self.report_date = row.report_date
        self.source_document_id = row.source_document_id
        self.matched = False


def load_truth(path: Path) -> list[TruthEvent]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "events" in data:
        data = data["events"]
    return [TruthEvent(d) for d in data]


def load_extracted(well_ids: Optional[list[str]] = None) -> list[ExtractedEvent]:
    # Ground-truth rows live in the same table (extraction_method == synthetic_truth) so the
    # dashboard can show them behind a toggle; they must never be scored as extractions.
    rows = [r for r in db.events_for(well_ids) if r.extraction_method != "synthetic_truth"]
    return [ExtractedEvent(r) for r in rows]


def _same_doc_or_date(t: TruthEvent, e: ExtractedEvent) -> bool:
    if t.source_document_id and e.source_document_id:
        return t.source_document_id == e.source_document_id
    if t.report_date and e.report_date:
        return t.report_date == e.report_date
    # If neither side carries a doc/date reference, don't disqualify on this axis.
    return True


def _depth_diff(t: TruthEvent, e: ExtractedEvent) -> Optional[float]:
    if t.depth_md_m is None or e.depth_md_m is None:
        return None
    return abs(t.depth_md_m - e.depth_md_m)


def match(truth: list[TruthEvent], extracted: list[ExtractedEvent]) -> list[tuple[TruthEvent, ExtractedEvent, Optional[float]]]:
    """Greedy nearest-depth matching within each (well_id, event_type) group."""
    matches: list[tuple[TruthEvent, ExtractedEvent, Optional[float]]] = []
    used_truth: set[int] = set()

    groups: dict[tuple, list[int]] = {}
    for j, e in enumerate(extracted):
        groups.setdefault((e.well_id, e.event_type), []).append(j)

    candidates = []
    for i, t in enumerate(truth):
        for j in groups.get((t.well_id, t.event_type), []):
            e = extracted[j]
            if e.matched:
                continue
            diff = _depth_diff(t, e)
            if diff is not None and diff > DEPTH_TOL_M:
                continue
            if not _same_doc_or_date(t, e):
                continue
            candidates.append((diff if diff is not None else float("inf"), i, j))

    candidates.sort(key=lambda c: c[0])
    for diff, i, j in candidates:
        if i in used_truth or extracted[j].matched:
            continue
        used_truth.add(i)
        extracted[j].matched = True
        matches.append((truth[i], extracted[j], None if diff == float("inf") else diff))

    return matches


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def evaluate(truth: list[TruthEvent], extracted: list[ExtractedEvent]) -> dict:
    matches = match(truth, extracted)
    matched_truth_idx = {id(m[0]) for m in matches}
    matched_extr_idx = {id(m[1]) for m in matches}

    tp = len(matches)
    fp = sum(1 for e in extracted if id(e) not in matched_extr_idx)
    fn = sum(1 for t in truth if id(t) not in matched_truth_idx)
    precision, recall, f1 = _prf(tp, fp, fn)

    per_type: dict[str, dict] = {}
    types = sorted({t.event_type for t in truth} | {e.event_type for e in extracted})
    for et in types:
        t_et = [t for t in truth if t.event_type == et]
        e_et = [e for e in extracted if e.event_type == et]
        m_et = [m for m in matches if m[0].event_type == et]
        tp_et = len(m_et)
        fp_et = sum(1 for e in e_et if id(e) not in matched_extr_idx)
        fn_et = sum(1 for t in t_et if id(t) not in matched_truth_idx)
        p, r, f = _prf(tp_et, fp_et, fn_et)
        per_type[et] = {"tp": tp_et, "fp": fp_et, "fn": fn_et, "precision": p, "recall": r, "f1": f}

    formation_hits = 0
    formation_total = 0
    depth_errors = []
    for t, e, diff in matches:
        if t.formation is not None:
            formation_total += 1
            if canonical_formation(t.formation) == canonical_formation(e.formation):
                formation_hits += 1
        if diff is not None:
            depth_errors.append(diff)

    formation_accuracy = formation_hits / formation_total if formation_total else None
    depth_mae = sum(depth_errors) / len(depth_errors) if depth_errors else None

    return {
        "n_truth": len(truth),
        "n_extracted": len(extracted),
        "overall": {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1},
        "per_event_type": per_type,
        "formation_accuracy": formation_accuracy,
        "depth_mae_m": depth_mae,
        "n_depth_comparisons": len(depth_errors),
    }


def _print_table(metrics: dict) -> None:
    o = metrics["overall"]
    print(f"\nOverall: precision={o['precision']:.3f}  recall={o['recall']:.3f}  f1={o['f1']:.3f}  "
          f"(tp={o['tp']} fp={o['fp']} fn={o['fn']}, n_truth={metrics['n_truth']}, n_extracted={metrics['n_extracted']})")
    fa = metrics["formation_accuracy"]
    dm = metrics["depth_mae_m"]
    print(f"Formation accuracy: {fa:.3f}" if fa is not None else "Formation accuracy: n/a (no matches with formation)")
    print(f"Depth MAE: {dm:.1f} m (n={metrics['n_depth_comparisons']})" if dm is not None else "Depth MAE: n/a")
    print(f"\n{'event_type':<20}{'tp':>5}{'fp':>5}{'fn':>5}{'precision':>11}{'recall':>9}{'f1':>7}")
    for et, m in metrics["per_event_type"].items():
        print(f"{et:<20}{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}{m['precision']:>11.3f}{m['recall']:>9.3f}{m['f1']:>7.3f}")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate extracted events against ground truth.")
    parser.add_argument("--truth", type=str, required=True)
    parser.add_argument("--well-ids", type=str, nargs="*", default=None,
                         help="Restrict to these well_ids; default = all wells present in the truth file")
    parser.add_argument("--out", type=str, default=str(settings.models_dir / "extraction_metrics.json"))
    parser.add_argument("--depth-tol", type=float, default=None,
                        help="Depth match window in metres (default 25). Use a day's drilled interval "
                             "(e.g. 150) when the truth file only carries day-level depths, as the Volve "
                             "weak labels do — and say so wherever the number is quoted.")
    args = parser.parse_args(argv)
    if args.depth_tol is not None:
        global DEPTH_TOL_M
        DEPTH_TOL_M = float(args.depth_tol)
        print(f"depth tolerance overridden: ±{DEPTH_TOL_M:.0f} m")

    truth = load_truth(Path(args.truth))
    well_ids = args.well_ids or sorted({t.well_id for t in truth if t.well_id})
    extracted = load_extracted(well_ids or None)

    metrics = evaluate(truth, extracted)
    _print_table(metrics)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
