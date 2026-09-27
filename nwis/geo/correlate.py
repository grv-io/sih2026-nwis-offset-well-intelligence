"""Correlation-panel data assembly + the dip-corrected lookahead that feeds
Phase 6 alerts (live/alerts.py reads active_well_lookahead's output).

Two views per well:
  - "tvd" (raw): formation bands and events at their true TVD.
  - "normalised": the same, keyed by formation name + offset-from-top, so
    wells with different absolute depths line up on formation (A11/Phase 3
    exit criterion).
"""
from __future__ import annotations

from typing import Optional

from nwis import db
from nwis.geo import dip, trajectory
from nwis.geo.nearby import nearby_wells


def formation_offset(well_id: str, md: float) -> tuple[Optional[str], Optional[float]]:
    """(formation, MD offset from that formation's top) containing `md`, or
    (None, None) if `md` falls outside any known top for this well."""
    tops = db.tops_for(well_id)  # already sorted by top_md_m
    if not tops:
        return None, None
    for i, top in enumerate(tops):
        base = top.base_md_m
        if base is None:
            base = tops[i + 1].top_md_m if i + 1 < len(tops) else float("inf")
        if top.top_md_m <= md < base:
            return top.formation, md - top.top_md_m
    last = tops[-1]
    if last.base_md_m is None and md >= last.top_md_m:
        return last.formation, md - last.top_md_m
    return None, None


def _event_tvd(well_id: str, e) -> Optional[float]:
    if e.depth_tvd_m is not None:
        return e.depth_tvd_m
    if e.depth_md_m is not None:
        return trajectory.md_to_tvd(well_id, e.depth_md_m)
    return None


def correlation_panel_data(well_ids: list[str]) -> dict:
    """Per well: formation bands (top/base TVD) + events (tvd, type, severity,
    page_ref, hours_lost); plus a "normalised" index keyed by formation name
    so callers (panel.correlation_figure mode="normalised") can align wells.
    """
    panel: dict = {"wells": {}, "normalised": {}}

    for wid in well_ids:
        tops = db.tops_for(wid)
        bands = []
        for i, t in enumerate(tops):
            base_tvd = t.base_tvd_m
            if base_tvd is None and i + 1 < len(tops):
                base_tvd = tops[i + 1].top_tvd_m
            bands.append({"formation": t.formation, "top_tvd_m": t.top_tvd_m, "base_tvd_m": base_tvd})

        events_out = []
        for e in db.events_for([wid]):
            tvd = _event_tvd(wid, e)
            events_out.append({
                "event_id": e.event_id,
                "tvd": tvd,
                "type": e.event_type,
                "severity": e.severity,
                "page_ref": e.source_page_ref,
                "hours_lost": e.hours_lost_npt,
                "formation": e.formation,
                "cause": e.cause,
                "remedy": e.remedy,
            })

        panel["wells"][wid] = {"bands": bands, "events": events_out}

        for band in bands:
            entry = panel["normalised"].setdefault(band["formation"], {}).setdefault(wid, {"events": []})
            entry["top_tvd_m"] = band["top_tvd_m"]
            entry["base_tvd_m"] = band["base_tvd_m"]

        for ev in events_out:
            if not ev["formation"] or ev["tvd"] is None:
                continue
            top_match = next((b for b in bands if b["formation"] == ev["formation"]), None)
            offset = ev["tvd"] - top_match["top_tvd_m"] if top_match else None
            entry = panel["normalised"].setdefault(ev["formation"], {}).setdefault(wid, {"events": []})
            entry.setdefault("events", []).append({**ev, "offset_from_top_m": offset})

    return panel


def active_well_lookahead(
    active_well_id: str, current_md: float, lookahead_m: float = 200.0, radius_km: float = 3.0
) -> list[dict]:
    """Offset-well events that, after dip-corrected depth mapping onto the
    active well, fall in the *upcoming* MD interval
    [current_md, current_md + lookahead_m]. Feeds Phase 6 alerts.
    """
    candidates = nearby_wells(active_well_id, radius_km)
    window_lo, window_hi = current_md, current_md + lookahead_m

    hits: list[dict] = []
    for cand in candidates:
        tops_b = db.tops_for(cand.well_id)
        for e in db.events_for([cand.well_id]):
            if not e.formation:
                continue
            top_b = next((t for t in tops_b if t.formation == e.formation), None)
            if top_b is None:
                continue
            tvd_event = _event_tvd(cand.well_id, e)
            if tvd_event is None:
                continue
            offset_tvd = tvd_event - top_b.top_tvd_m

            expected_top_tvd, n_used, method = dip.predict_top_at(active_well_id, e.formation)
            if expected_top_tvd is None:
                continue
            expected_tvd = expected_top_tvd + offset_tvd
            expected_md = trajectory.tvd_to_md(active_well_id, expected_tvd)

            if window_lo <= expected_md <= window_hi:
                hits.append({
                    "source_well_id": cand.well_id,
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "severity": e.severity,
                    "formation": e.formation,
                    "expected_md_m": expected_md,
                    "expected_tvd_m": expected_tvd,
                    "page_ref": e.source_page_ref,
                    "cause": e.cause,
                    "remedy": e.remedy,
                    "hours_lost": e.hours_lost_npt,
                    "dip_method": method,
                    "dip_n_wells": n_used,
                })

    hits.sort(key=lambda h: h["expected_md_m"])
    return hits
