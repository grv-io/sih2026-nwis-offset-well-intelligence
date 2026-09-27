"""Phase 7 (web frontend) API surface: the few read/write endpoints the React UI
needs that the Phase 3/4/6 routers don't already provide.

Mounted in api/main.py at the "# Phase 7" hook. Handlers stay thin: every read
goes through nwis.db readers / nwis.geo functions; the only logic here is
(a) the event-source filter (extracted vs synthetic ground truth), which the UI
must be able to toggle but nwis.geo does not know about, and (b) resolving a
citation string back to the document text it points at (the "Investigate
historical solutions" drawer, IMPLEMENTATION_PLAN §6 A6).

Event source semantics (UI default = extracted):
    extracted -> extraction_method != "synthetic_truth"  (what the pipeline read from PDFs)
    truth     -> extraction_method == "synthetic_truth"  (generator labels, judges only)
    all       -> both
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Literal, Optional, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from nwis import db
from nwis.config import settings
from nwis.geo import correlate, panel
from nwis.geo.distance import circle_polygon
from nwis.geo.nearby import nearby_wells
from nwis.schema import FORMATIONS, EventType

router = APIRouter(tags=["ui"])

TRUTH = "synthetic_truth"
REVIEW_THRESHOLD = 0.6
Source = Literal["extracted", "truth", "all"]
Basin = Literal["assam", "volve"]
T = TypeVar("T")

# --------------------------------------------------------------------------- #
# Volve ("second basin") — a real North Sea field loaded into its OWN sqlite
# file (data/volve.sqlite, built by nwis.external.volve.wells) so it never
# touches data/nwis.sqlite. Per IMPLEMENTATION_PLAN Phase 10, opened read-only
# here and NEVER via nwis.db.engine(path) (that call repoints the module-global
# engine every other handler in this process shares — doing that inside a
# request handler would make every other endpoint in a concurrent request
# start reading/writing the wrong database).
VOLVE_DB_PATH = settings.data_dir / "volve.sqlite"


def _volve_conn() -> sqlite3.Connection:
    if not VOLVE_DB_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Volve database not found at {VOLVE_DB_PATH}. "
                "Run `python -m nwis.external.volve.wells` (after `download`/`prepare_docs`) to build it."
            ),
        )
    conn = sqlite3.connect(f"file:{VOLVE_DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _volve_wells_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT well_id, name, field, lat, lon, kb_elev_m, spud_date, td_md_m, "
        "trajectory_type, status, basin FROM wells ORDER BY well_id"
    ).fetchall()


def _volve_events_rows(conn: sqlite3.Connection, well_ids: Optional[list[str]] = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM events"
    params: list[str] = []
    if well_ids:
        q += f" WHERE well_id IN ({','.join('?' for _ in well_ids)})"
        params = well_ids
    return conn.execute(q, params).fetchall()


def _volve_event_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["reviewed_by_human"] = bool(d.get("reviewed_by_human"))
    d["is_ground_truth"] = False  # Volve has no synthetic_truth rows -- real reports only.
    return d

# Hazard families used for map flag-dot colour (3 categorical slots + neutral
# "other": a map is an all-pairs scatter, so more than 3 hues stops being
# distinguishable; the exact dominant type is still returned per well).
HAZARD_FAMILY: dict[str, str] = {
    "mud_loss": "losses",
    "stuck_pipe": "stuck", "wellbore_instability": "stuck", "fishing_operation": "stuck",
    "twist_off": "stuck", "lost_bha": "stuck",
    "kick": "well_control", "overpressure": "well_control", "gas_show": "well_control",
    "cementing_issue": "other", "torque_spike": "other", "npt_other": "other",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _retry(fn: Callable[[], T], attempts: int = 6, delay_s: float = 0.25) -> T:
    """The ingestion job writes the same SQLite file; retry briefly on a lock."""
    for i in range(attempts):
        try:
            return fn()
        except OperationalError as e:  # "database is locked"
            if "locked" not in str(e).lower() or i == attempts - 1:
                raise
            time.sleep(delay_s * (i + 1))
    raise RuntimeError("unreachable")


def _split(csv: Optional[str]) -> list[str]:
    return [x.strip() for x in (csv or "").split(",") if x.strip()]


def _source_ok(method: str, source: str) -> bool:
    if source == "all":
        return True
    is_truth = method == TRUTH
    return is_truth if source == "truth" else not is_truth


def _events(well_ids: Optional[list[str]] = None, source: str = "extracted",
            event_type: Optional[str] = None, formation: Optional[str] = None) -> list[db.EventRow]:
    rows = _retry(lambda: db.events_for(well_ids or None, event_type=event_type, formation=formation))
    return [e for e in rows if _source_ok(e.extraction_method, source)]


def _event_dict(e: db.EventRow) -> dict:
    d = e.model_dump()
    d["report_date"] = e.report_date.isoformat() if e.report_date else None
    d["is_ground_truth"] = e.extraction_method == TRUTH
    return d


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
@router.get("/events")
def list_events(
    well_ids: Optional[str] = Query(None, description="Comma-separated well ids"),
    source: Source = "extracted",
    event_type: Optional[str] = None,
    formation: Optional[str] = None,
    ids: Optional[str] = Query(None, description="Comma-separated event ids (overrides other filters except source)"),
    basin: Basin = "assam",
) -> list[dict]:
    if basin == "volve":
        conn = _volve_conn()
        try:
            wanted = set(_split(ids)) if ids else None
            rows = _volve_events_rows(conn, _split(well_ids) or None)
        finally:
            conn.close()
        out = []
        for r in rows:
            if wanted is not None and r["event_id"] not in wanted:
                continue
            if not _source_ok(r["extraction_method"], source):
                continue
            if event_type and r["event_type"] != event_type:
                continue
            if formation and r["formation"] != formation:
                continue
            out.append(_volve_event_dict(r))
        out.sort(key=lambda e: (e["well_id"], e["depth_md_m"] if e["depth_md_m"] is not None else 1e9))
        return out

    if ids:
        wanted = set(_split(ids))
        rows = [e for e in _retry(lambda: db.events_for()) if e.event_id in wanted]
        rows = [e for e in rows if _source_ok(e.extraction_method, source)]
    else:
        rows = _events(_split(well_ids), source, event_type, formation)
    rows.sort(key=lambda e: (e.well_id, e.depth_md_m if e.depth_md_m is not None else 1e9))
    return [_event_dict(e) for e in rows]


@router.get("/ui/summary")
def ui_summary() -> dict:
    """Counts for the header / empty states; also tells the UI which optional
    backends (risk router, search index) are present."""
    rows = _retry(lambda: db.events_for())
    n_truth = sum(1 for e in rows if e.extraction_method == TRUTH)
    extracted = [e for e in rows if e.extraction_method != TRUTH]
    docs = _retry(db.documents_for)
    chunks = _retry(db.all_chunks)
    return {
        "wells": len(_retry(db.all_wells)),
        "events_truth": n_truth,
        "events_extracted": len(extracted),
        "documents": len(docs),
        "documents_ingested": len({c.document_id for c in chunks}),
        "chunks": len(chunks),
        "formations": FORMATIONS,
        "event_types": [t.value for t in EventType],
        "hazard_family": HAZARD_FAMILY,
    }


# --------------------------------------------------------------------------- #
# review queue
# --------------------------------------------------------------------------- #
@router.get("/review-queue")
def get_review_queue() -> dict:
    """Extracted events awaiting a human: anything the ingest pipeline queued
    (review_queue table) plus any extracted, unreviewed event under the
    confidence threshold that is not queued (belt and braces)."""
    queued = _retry(db.review_queue)
    reason_by_id = {q.event_id: q.reason for q in queued}
    extracted = [e for e in _retry(lambda: db.events_for()) if e.extraction_method != TRUTH]
    by_id = {e.event_id: e for e in extracted}

    pending_ids = {eid for eid in reason_by_id if eid in by_id and not by_id[eid].reviewed_by_human}
    pending_ids |= {e.event_id for e in extracted
                    if not e.reviewed_by_human and e.extraction_confidence < REVIEW_THRESHOLD}

    items = []
    for eid in pending_ids:
        e = by_id[eid]
        d = _event_dict(e)
        d["reason"] = reason_by_id.get(eid, f"confidence {e.extraction_confidence:.2f}")
        items.append(d)
    items.sort(key=lambda d: (d["extraction_confidence"], d["well_id"]))

    return {
        "threshold": REVIEW_THRESHOLD,
        "counters": {
            "extracted": len(extracted),
            "reviewed": sum(1 for e in extracted if e.reviewed_by_human),
            "pending": len(items),
        },
        "items": items,
    }


def _resolve_review_rows(s, event_id: str) -> None:
    for row in s.exec(db.select(db.ReviewRow).where(db.ReviewRow.event_id == event_id)).all():
        row.resolved = True
        s.add(row)


@router.post("/review-queue/{event_id}/approve")
def approve_event(event_id: str) -> dict:
    def _do():
        with db.session() as s:
            ev = s.get(db.EventRow, event_id)
            if ev is None:
                return None
            ev.reviewed_by_human = True
            s.add(ev)
            _resolve_review_rows(s, event_id)
            s.commit()
            return True
    if _retry(_do) is None:
        raise HTTPException(status_code=404, detail=f"event {event_id!r} not found")
    return {"status": "approved", "event_id": event_id, "reviewed_by_human": True}


@router.post("/review-queue/{event_id}/reject")
def reject_event(event_id: str) -> dict:
    def _do():
        with db.session() as s:
            ev = s.get(db.EventRow, event_id)
            if ev is None:
                return None
            if ev.extraction_method == TRUTH:
                return "truth"
            s.delete(ev)
            _resolve_review_rows(s, event_id)
            s.commit()
            return True
    res = _retry(_do)
    if res is None:
        raise HTTPException(status_code=404, detail=f"event {event_id!r} not found")
    if res == "truth":
        raise HTTPException(status_code=409, detail="ground-truth events cannot be rejected")
    return {"status": "rejected", "event_id": event_id}


# --------------------------------------------------------------------------- #
# documents (citation drill-down)
# --------------------------------------------------------------------------- #
_PAGE_RE = re.compile(r"#p(\d+)")
_STEM_RE = re.compile(r"([A-Z]{2,4}-\d{2,4}_[A-Z]{2,4}_[\w\-]+?)(?:\.(?:pdf|txt))?(?:#p\d+)?(?=$|[\s|\"'\]])")


def _find_doc(ref: str) -> Optional[db.DocRow]:
    """document_id exact, else a page_ref / filename stem ("DUL-001_DDR_2017-06-12.pdf#p1")."""
    docs = _retry(db.documents_for)
    by_id = {d.document_id: d for d in docs}
    if ref in by_id:
        return by_id[ref]
    stem = re.sub(r"#p\d+$", "", ref.strip())
    name_hits = [d for d in docs if Path(d.path.replace("\\", "/")).name == stem]
    if name_hits:
        return name_hits[0]
    stem = re.sub(r"\.(pdf|txt)$", "", stem)
    if stem in by_id:
        return by_id[stem]
    stem_hits = [d for d in docs if Path(d.path.replace("\\", "/")).stem == stem]
    # prefer the canonical (generator) record, then a text document
    stem_hits.sort(key=lambda d: (d.document_id != stem, not d.path.endswith(".txt")))
    return stem_hits[0] if stem_hits else None


def _abs(path: str) -> Path:
    p = Path(path.replace("\\", "/"))
    return p if p.is_absolute() else settings.root / p


def _doc_text(doc: db.DocRow, page: int) -> tuple[str, str]:
    """(text, source) for one page: ingested chunks -> sibling .txt -> pdf text
    layer -> cached OCR json."""
    chunks = [c for c in _retry(db.all_chunks) if c.document_id == doc.document_id]
    page_chunks = [c for c in chunks if (m := _PAGE_RE.search(c.page_ref)) and int(m.group(1)) == page]
    if page_chunks:
        return "\n\n".join(c.text for c in page_chunks), "ingested_chunks"

    p = _abs(doc.path)
    txt = p.with_suffix(".txt")
    if txt.exists():
        pages = txt.read_text(encoding="utf-8", errors="replace").split("\f")
        return pages[min(page, len(pages)) - 1], "report_text"
    if p.suffix.lower() == ".pdf" and p.exists():
        try:
            import pdfplumber
            with pdfplumber.open(str(p)) as pdf:
                if 1 <= page <= len(pdf.pages):
                    t = pdf.pages[page - 1].extract_text() or ""
                    if len(t.strip()) >= 20:
                        return t, "pdf_text_layer"
        except Exception:  # noqa: BLE001 — a scanned/odd pdf falls through to OCR cache
            pass
        ocr = p.with_name(p.name + ".ocr.json")
        if ocr.exists():
            try:
                data = json.loads(ocr.read_text(encoding="utf-8"))
                val = data.get(str(page)) if isinstance(data, dict) else (
                    data[page - 1] if isinstance(data, list) and len(data) >= page else None)
                if isinstance(val, str):
                    return val, "ocr_cache"
            except Exception:  # noqa: BLE001
                pass
    return "", "unavailable"


def _doc_payload(doc: db.DocRow, page: int) -> dict:
    text, src = _doc_text(doc, page)
    return {
        "document_id": doc.document_id,
        "well_id": doc.well_id,
        "doc_type": doc.doc_type,
        "report_date": doc.report_date.isoformat() if doc.report_date else None,
        "path": doc.path.replace("\\", "/"),
        "file_name": Path(doc.path.replace("\\", "/")).name,
        "is_scanned": doc.is_scanned,
        "n_pages": doc.n_pages,
        "page": page,
        "text_source": src,
        "text": text,
    }


@router.get("/documents/resolve")
def resolve_document(ref: str = Query(..., description="document_id, page_ref, or a citation string")) -> dict:
    """Citation string -> {document_id, page}. Accepts the shapes the backend
    emits: '[DUL-003 | DUL-003_DDR_2019-01-02.pdf#p1]', 'well | doc | page — "quote"',
    a bare page_ref, or a document_id."""
    candidates = [ref.strip()]
    candidates += [p.strip() for p in re.split(r"[|\[\]—]", ref) if p.strip()]
    candidates += [m.group(0) for m in _STEM_RE.finditer(ref)]
    for c in candidates:
        doc = _find_doc(c)
        if doc is not None:
            m = _PAGE_RE.search(ref)
            return {"document_id": doc.document_id, "well_id": doc.well_id,
                    "page": int(m.group(1)) if m else 1, "ref": ref}
    raise HTTPException(status_code=404, detail=f"no document matches {ref!r}")


@router.get("/documents/{document_id}/text")
def document_text(document_id: str, page: int = Query(1, ge=1)) -> dict:
    doc = _find_doc(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"document {document_id!r} not found")
    return _doc_payload(doc, page)


# --------------------------------------------------------------------------- #
# correlation figure + map data
# --------------------------------------------------------------------------- #
def _add_formation_bands(fig, data: dict, mode: str) -> None:
    """panel.correlation_figure adds its formation bands with add_hrect(row, col)
    *before* any trace exists, and Plotly's default exclude_empty_subplots=True
    silently drops them, so the figure arrives with no bands at all. Re-add them
    here with the same colours/geometry (only when absent, so a fix upstream in
    nwis/geo/panel.py makes this a no-op), plus formation labels in TVD mode."""
    for col, wid in enumerate(data["wells"], start=1):
        bands = data["wells"][wid]["bands"]
        for band in bands:
            f = band["formation"]
            if mode == "normalised":
                idx = panel.FORMATION_ORDER.get(f, len(panel.FORMATION_ORDER))
                y0, y1 = idx, idx + 1
            else:
                y0 = band["top_tvd_m"]
                y1 = band["base_tvd_m"] if band["base_tvd_m"] is not None else y0 + 1.0
            fig.add_hrect(y0=y0, y1=y1, fillcolor=panel.FORMATION_COLORS.get(f, "#cccccc"),
                          opacity=0.25, line_width=0, row=1, col=col, exclude_empty_subplots=False)
            if mode != "normalised" and col == 1 and y1 - y0 > 60:
                fig.add_annotation(x=0.03, xref="x domain", y=(y0 + y1) / 2, yref="y", text=f,
                                   showarrow=False, xanchor="left", font=dict(size=10), opacity=0.85)


@router.get("/correlation/figure")
def correlation_figure(well_ids: str, mode: Literal["tvd", "normalised"] = "tvd",
                       source: Source = "extracted") -> dict:
    ids = _split(well_ids)
    if not ids:
        raise HTTPException(status_code=400, detail="well_ids must be a non-empty comma-separated list")
    data = _retry(lambda: correlate.correlation_panel_data(ids))
    keep = {e.event_id for e in _events(ids, source)}
    n_events = 0
    for w in data["wells"].values():
        w["events"] = [ev for ev in w["events"] if ev["event_id"] in keep]
        n_events += len(w["events"])
    for per_well in data["normalised"].values():
        for entry in per_well.values():
            entry["events"] = [ev for ev in entry.get("events", []) if ev["event_id"] in keep]
    fig = panel.correlation_figure(data, mode=mode)
    if not fig.layout.shapes:
        _add_formation_bands(fig, data, mode)
    return {
        "figure": json.loads(fig.to_json()),  # == fig.to_plotly_json(), JSON-safe (numpy -> lists)
        "well_ids": ids, "mode": mode, "source": source, "n_events": n_events,
    }


def _volve_map_data(well_id: Optional[str], radius_km: float, source: Source) -> dict:
    conn = _volve_conn()
    try:
        wells = _volve_wells_rows(conn)
        events = _volve_events_rows(conn)
    finally:
        conn.close()

    by_well: dict[str, Counter] = {}
    for e in events:
        if not _source_ok(e["extraction_method"], source):
            continue
        by_well.setdefault(e["well_id"], Counter())[e["event_type"]] += 1

    out_wells = []
    for w in wells:
        counts = by_well.get(w["well_id"], Counter())
        dominant = counts.most_common(1)[0][0] if counts else None
        out_wells.append({
            "well_id": w["well_id"], "name": w["name"], "field": w["field"], "lat": w["lat"], "lon": w["lon"],
            "td_md_m": w["td_md_m"], "trajectory_type": w["trajectory_type"], "status": w["status"],
            "spud_date": w["spud_date"],
            "event_counts": dict(counts), "n_events": sum(counts.values()),
            "dominant_event_type": dominant,
            "dominant_family": HAZARD_FAMILY.get(dominant) if dominant else None,
        })

    active, ring = None, []
    if well_id:
        a = next((w for w in wells if w["well_id"] == well_id), None)
        if a is None:
            raise HTTPException(status_code=404, detail=f"well {well_id!r} not found in the Volve basin")
        active = well_id
        ring = [[lat, lon] for lat, lon in circle_polygon(a["lat"], a["lon"], radius_km)]

    return {
        "active_well_id": active, "radius_km": radius_km, "source": source,
        "wells": out_wells, "candidates": [], "ring": ring,
        "note": "similarity ranking needs formation tops; not available for Volve",
    }


@router.get("/map/data")
def map_data(well_id: Optional[str] = None, radius_km: float = Query(3.0, gt=0, le=50),
             source: Source = "extracted", basin: Basin = "assam") -> dict:
    if basin == "volve":
        return _volve_map_data(well_id, radius_km, source)

    wells = _retry(db.all_wells)
    by_well: dict[str, Counter] = {}
    for e in _events(None, source):
        by_well.setdefault(e.well_id, Counter())[e.event_type] += 1

    out_wells = []
    for w in wells:
        counts = by_well.get(w.well_id, Counter())
        dominant = counts.most_common(1)[0][0] if counts else None
        out_wells.append({
            "well_id": w.well_id, "name": w.name, "field": w.field, "lat": w.lat, "lon": w.lon,
            "td_md_m": w.td_md_m, "trajectory_type": w.trajectory_type, "status": w.status,
            "spud_date": w.spud_date.isoformat() if w.spud_date else None,
            "event_counts": dict(counts), "n_events": sum(counts.values()),
            "dominant_event_type": dominant,
            "dominant_family": HAZARD_FAMILY.get(dominant) if dominant else None,
        })

    active, candidates, ring = None, [], []
    if well_id:
        a = _retry(lambda: db.get_well(well_id))
        if a is None:
            raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
        active = well_id
        candidates = [c.model_dump() for c in _retry(lambda: nearby_wells(well_id, radius_km))]
        ring = [[lat, lon] for lat, lon in circle_polygon(a.lat, a.lon, radius_km)]

    return {"active_well_id": active, "radius_km": radius_km, "source": source,
            "wells": out_wells, "candidates": candidates, "ring": ring}


# --------------------------------------------------------------------------- #
# keyword-only search fallback (no embedding call -> works while Ollama is busy)
# --------------------------------------------------------------------------- #
@router.get("/search/keyword")
def keyword_search(q: str, k: int = Query(8, ge=1, le=50),
                   well_ids: Optional[str] = None) -> list[dict]:
    from nwis.search.retrieve import sanitize_fts_query

    fq = sanitize_fts_query(q)
    if not fq:
        return []
    ids = _retry(lambda: db.fts_search(fq, limit=max(k * 4, 20)))
    rows = {c.chunk_id: c for c in _retry(db.all_chunks)}
    allowed = set(_split(well_ids)) or None
    out = []
    for rank, cid in enumerate(ids):
        c = rows.get(cid)
        if c is None or (allowed and c.well_id not in allowed):
            continue
        out.append({"chunk_id": c.chunk_id, "well_id": c.well_id, "page_ref": c.page_ref,
                    "document_id": c.document_id, "text": c.text,
                    "score": 1.0 / (60 + rank + 1), "why": f"fts#{rank + 1} (keyword only)"})
        if len(out) >= k:
            break
    return out


# --------------------------------------------------------------------------- #
# rig view: slim live state for VSAT links (read-only view of the Phase 6 session)
# --------------------------------------------------------------------------- #
_SPARK_COLS = ("t_s", "depth_md_m", "torque_kftlb", "pit_vol_bbl", "gas_pct", "rop_m_hr",
               "spp_psi", "flow_in_gpm", "flow_out_gpm", "wob_klbf", "mw_ppg")


def _clean(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if f != f else round(f, 3)  # NaN -> null, trim payload


@router.get("/ui/live-state")
def live_state_slim(window: int = Query(60, ge=0, le=600)) -> dict:
    """/live/state without the alert lists (poll /live/alerts for those), plus
    run flags and the last `window` samples for the rig sparklines."""
    from api import live_router

    sess = live_router._session
    if sess is None:
        raise HTTPException(status_code=409, detail="no active replay session; call POST /live/start first")
    last = sess._last_sample or {}
    samples = list(sess.window)[-window:] if window else []
    n_total = len(sess.df)
    return {
        "well_id": sess.well_id,
        "speed": sess.speed,
        "running": bool(sess._running and sess._task is not None),
        "paused": bool(sess._paused),
        "finished": sess._idx >= n_total,
        "progress": (sess._idx / n_total) if n_total else 0.0,
        "md_range": [float(sess.df["depth_md_m"].min()), float(sess.df["depth_md_m"].max())] if n_total else None,
        "t_s": _clean(last.get("t_s")),
        "depth_md_m": _clean(last.get("depth_md_m")),
        "formation": sess._formation,
        "last_sample": {k: _clean(last.get(k)) for k in _SPARK_COLS if k in last},
        "window": [{k: _clean(s.get(k)) for k in _SPARK_COLS if k in s} for s in samples],
        "n_alerts_open": len(sess.alert_engine.open_alerts(include_acked=False)),
        "signals_unavailable": sorted(sess.alert_engine.signals_unavailable),
    }


class _StartAt(BaseModel):
    well_id: str
    speed: float = 200.0
    start_md: Optional[float] = None


@router.post("/ui/live/start")
async def live_start_at(req: _StartAt) -> dict:
    """POST /live/start + /live/seek composed in the right order: seek BEFORE the
    replay task starts, so a demo run beginning at e.g. 2,350 m doesn't first
    replay (and alert on) every metre from surface. Same single-session slot as
    api/live_router.py. async for the same reason as that router (event loop)."""
    from api import live_router
    from nwis.live.replay import ReplaySession

    if db.get_well(req.well_id) is None:
        raise HTTPException(status_code=404, detail=f"well {req.well_id!r} not found")
    if live_router._session is not None:
        live_router._session.stop()
    sess = ReplaySession(well_id=req.well_id, speed=req.speed)
    if req.start_md and req.start_md > 0:
        sess.seek(req.start_md)
    live_router._session = sess
    sess.start()
    return {"status": "started", "well_id": req.well_id, "speed": req.speed, "start_md": req.start_md}
