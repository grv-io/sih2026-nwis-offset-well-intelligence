"""Incident linking: merge mentions of the same incident found in different documents.

A drilling problem is normally written up twice: in the Daily Drilling Report on the day it
happened and again in the Well Completion Report's "problems encountered" section. Both are
correct extractions, but for offset-precedent counts, alerts and evaluation they are ONE incident.
(26 Sep 2026 eval on the synthetic field: 348 extracted, 182 truth, 149 DDR/WCR pairs.)

Rule: same well, same event_type, |depth difference| <= DEPTH_TOL_M (or both depths missing and same
report_date) -> keep one canonical row, delete the other, and record the dropped mention's page ref
in the canonical row's free_text so provenance survives. Canonical preference: DDR over WCR (the
day-of narrative is more specific), then higher extraction_confidence, then earlier report_date.
Ground-truth and human-reviewed rows are never touched.

    python -m nwis.ingest.link [--dry-run]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Optional

from sqlalchemy import text as sql_text

from nwis import db

DEPTH_TOL_M = 25.0
DOC_PRIORITY = {"DDR": 0, "mud_log": 1, "WCR": 2, "other": 3}
PROTECTED = ("synthetic_truth", "human_reviewed")


def _rank(ev: db.EventRow, doc_type: str) -> tuple:
    # date.max, not a string: a group mixing dated and undated events must stay sortable
    # (review 26 Sep: str-vs-date TypeError aborted linking for the whole corpus).
    from datetime import date as _date
    return (DOC_PRIORITY.get(doc_type, 9), -float(ev.extraction_confidence or 0.0),
            ev.report_date or _date.max, ev.event_id)


def _same_incident(a: db.EventRow, b: db.EventRow) -> bool:
    if a.well_id != b.well_id or a.event_type != b.event_type:
        return False
    if a.depth_md_m is not None and b.depth_md_m is not None:
        return abs(a.depth_md_m - b.depth_md_m) <= DEPTH_TOL_M
    if a.depth_md_m is None and b.depth_md_m is None:
        return a.report_date is not None and a.report_date == b.report_date
    return False


def link_incidents(dry_run: bool = False) -> dict:
    doc_types = {d.document_id: d.doc_type for d in db.documents_for()}
    events = [e for e in db.events_for() if e.extraction_method not in PROTECTED]
    by_key: dict[tuple, list[db.EventRow]] = defaultdict(list)
    for e in events:
        by_key[(e.well_id, e.event_type)].append(e)

    merged = 0
    to_delete: list[str] = []
    notes: dict[str, list[str]] = {}
    for group in by_key.values():
        group.sort(key=lambda e: _rank(e, doc_types.get(e.source_document_id, "other")))
        canon: list[db.EventRow] = []
        for e in group:
            target: Optional[db.EventRow] = next((c for c in canon if _same_incident(c, e)), None)
            if target is None:
                canon.append(e)
            else:
                to_delete.append(e.event_id)
                notes.setdefault(target.event_id, []).append(e.source_page_ref)
                merged += 1

    if not dry_run and to_delete:
        with db.engine().connect() as conn:
            for eid, refs in notes.items():
                conn.execute(sql_text(
                    "UPDATE events SET free_text = free_text || :n WHERE event_id = :e"
                ).bindparams(n=" [also reported in: " + "; ".join(refs) + "]", e=eid))
            # bound parameters, not string-formatted ids (defence in depth; ids are uuid5 today)
            conn.execute(sql_text("DELETE FROM review_queue WHERE event_id = :e"),
                         [{"e": x} for x in to_delete])
            conn.execute(sql_text("DELETE FROM events WHERE event_id = :e"),
                         [{"e": x} for x in to_delete])
            conn.commit()
    return {"mentions": len(events), "merged": merged, "incidents": len(events) - merged, "dry_run": dry_run}


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge cross-document mentions of the same drilling incident.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = link_incidents(dry_run=a.dry_run)
    print(f"incident linking: {r['mentions']} mentions -> {r['incidents']} incidents ({r['merged']} merged){' [dry-run]' if a.dry_run else ''}")


if __name__ == "__main__":
    main()
