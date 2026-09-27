"""Formation-name canonicalisation, MD->TVD, formation-at-depth, and the
LLMEvent -> DrillingEvent assembly step (adds provenance + normalised fields).
"""
from __future__ import annotations

import math
import uuid
from datetime import date
from typing import Optional

from rapidfuzz import fuzz, process

from nwis import db
from nwis.schema import (
    DrillingEvent,
    ExtractionMethod,
    FORMATION_ALIASES,
    FORMATIONS,
    LLMEvent,
)

FUZZY_THRESHOLD = 85


# --------------------------------------------------------------------------- #
def canonical_formation(name: Optional[str]) -> Optional[str]:
    """Resolve a formation name as written in a report to the canonical FORMATIONS
    entry, via FORMATION_ALIASES first, then rapidfuzz fuzzy matching (threshold 85).
    Returns None if nothing resolves confidently.
    """
    if not name or not name.strip():
        return None

    raw = name.strip()
    key = raw.lower()

    if key in FORMATION_ALIASES:
        return FORMATION_ALIASES[key]
    if raw in FORMATIONS:
        return raw

    best = process.extractOne(key, FORMATIONS, scorer=fuzz.WRatio, processor=str.lower)
    if best and best[1] >= FUZZY_THRESHOLD:
        return best[0]

    best_alias = process.extractOne(key, list(FORMATION_ALIASES.keys()), scorer=fuzz.WRatio)
    if best_alias and best_alias[1] >= FUZZY_THRESHOLD:
        return FORMATION_ALIASES[best_alias[0]]

    return None


# --------------------------------------------------------------------------- #
def _min_curvature_dtvd(md1: float, inc1: float, azi1: float, md2: float, inc2: float, azi2: float) -> float:
    """Delta-TVD between two survey stations via the minimum-curvature method."""
    dmd = md2 - md1
    if dmd <= 0:
        return 0.0
    inc1r, inc2r = math.radians(inc1), math.radians(inc2)
    azi1r, azi2r = math.radians(azi1), math.radians(azi2)
    cos_beta = math.cos(inc2r - inc1r) - math.sin(inc1r) * math.sin(inc2r) * (1 - math.cos(azi2r - azi1r))
    cos_beta = max(-1.0, min(1.0, cos_beta))
    beta = math.acos(cos_beta)
    rf = 1.0 if beta < 1e-9 else (2.0 / beta) * math.tan(beta / 2.0)
    return (dmd / 2.0) * (math.cos(inc1r) + math.cos(inc2r)) * rf


def md_to_tvd(well_id: str, md: float) -> float:
    """TVD at a given MD via minimum curvature over this well's surveys.
    Falls back to TVD == MD if the well has no surveys (or above the first
    station, where the hole is assumed vertical from surface).
    """
    stations_rows = db.surveys_for(well_id)
    if not stations_rows:
        return md

    pts = [(s.md_m, s.inc_deg, s.azi_deg) for s in stations_rows]
    pts.sort(key=lambda t: t[0])
    if pts[0][0] > 0:
        pts.insert(0, (0.0, 0.0, 0.0))

    if md <= pts[0][0]:
        return md

    tvd = 0.0
    for i in range(len(pts) - 1):
        md1, inc1, azi1 = pts[i]
        md2, inc2, azi2 = pts[i + 1]
        if md <= md2:
            frac = (md - md1) / (md2 - md1) if md2 > md1 else 0.0
            inc_t = inc1 + frac * (inc2 - inc1)
            azi_t = azi1 + frac * (azi2 - azi1)
            tvd += _min_curvature_dtvd(md1, inc1, azi1, md, inc_t, azi_t)
            return tvd
        tvd += _min_curvature_dtvd(md1, inc1, azi1, md2, inc2, azi2)

    # md beyond last station: hold the last inclination/azimuth.
    md_last, inc_last, azi_last = pts[-1]
    tvd += _min_curvature_dtvd(md_last, inc_last, azi_last, md, inc_last, azi_last)
    return tvd


# --------------------------------------------------------------------------- #
def formation_at_depth(well_id: str, md: float) -> Optional[str]:
    """Which formation (by top/base MD) contains this depth, per db formation_tops."""
    tops = db.tops_for(well_id)  # already sorted by top_md_m
    if not tops:
        return None
    for i, top in enumerate(tops):
        base = top.base_md_m
        if base is None:
            base = tops[i + 1].top_md_m if i + 1 < len(tops) else float("inf")
        if top.top_md_m <= md < base:
            return top.formation
    # deeper than the last known top with no base -> still "in" the last formation
    last = tops[-1]
    if last.base_md_m is None and md >= last.top_md_m:
        return last.formation
    return None


def offset_from_top(well_id: str, md: float) -> Optional[float]:
    """MD offset below the top of the formation containing `md`, or None if unresolved."""
    tops = db.tops_for(well_id)
    if not tops:
        return None
    for i, top in enumerate(tops):
        base = top.base_md_m
        if base is None:
            base = tops[i + 1].top_md_m if i + 1 < len(tops) else float("inf")
        if top.top_md_m <= md < base:
            return md - top.top_md_m
    last = tops[-1]
    if last.base_md_m is None and md >= last.top_md_m:
        return md - last.top_md_m
    return None


# --------------------------------------------------------------------------- #
def build_drilling_event(
    llm_event: LLMEvent,
    *,
    well_id: str,
    source_document_id: str,
    source_page_ref: str,
    report_date: Optional[date] = None,
    extraction_method: ExtractionMethod = ExtractionMethod.ocr_llm_local,
) -> DrillingEvent:
    """Attach provenance + normalised depth/formation fields to a raw LLMEvent."""
    depth_md = llm_event.depth_md_m
    depth_tvd = md_to_tvd(well_id, depth_md) if depth_md is not None else None

    formation = canonical_formation(llm_event.formation)
    if formation is None and depth_md is not None:
        # Fall back to the db's own formation-top model when the LLM's spelling
        # doesn't resolve (or wasn't stated) but we know the depth.
        formation = formation_at_depth(well_id, depth_md)

    offset = None
    if depth_md is not None and formation is not None:
        offset = offset_from_top(well_id, depth_md)

    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_document_id}|{source_page_ref}|{llm_event.quote}"))

    return DrillingEvent(
        event_id=event_id,
        well_id=well_id,
        source_document_id=source_document_id,
        source_page_ref=source_page_ref,
        report_date=report_date,
        depth_md_m=depth_md,
        depth_tvd_m=depth_tvd,
        interval_top_md_m=llm_event.interval_top_md_m,
        interval_base_md_m=llm_event.interval_base_md_m,
        formation=formation,
        formation_offset_from_top_m=offset,
        event_type=llm_event.event_type,
        severity=llm_event.severity,
        hours_lost_npt=llm_event.hours_lost_npt,
        cause=llm_event.cause,
        remedy=llm_event.remedy,
        mud_weight_ppg_at_event=llm_event.mud_weight_ppg_at_event,
        free_text=llm_event.quote,
        extraction_confidence=llm_event.confidence,
        extraction_method=extraction_method,
    )
