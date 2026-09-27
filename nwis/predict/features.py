"""Phase 5 — per-well, per-interval feature table (docs/IMPLEMENTATION_PLAN.md Phase 5 item 1).

For every well, from surface to TD in `nwis.config.settings.interval_m` steps:
  - formation identity + offset-from-top + fraction-of-formation (nwis.geo.correlate)
  - planned-MW-vs-formation-mud-weight-window (nwis.synth.hazards, skipped honestly if that
    module isn't importable, the formation has no window defined, or there's no log-derived
    actual MW to compare against)
  - offset precedent density per hazard within `radius_km` (default 3 km), using
    nwis.geo.nearby.nearby_wells for the candidate set + similarity weights, and every
    DrillingEvent (truth *and* extracted — this is what a real deployment would have) in
    those candidates, formation-matched and offset-matched (formation-normalised) against
    this interval
  - live-parameter stats (mean/max/slope of torque/spp/rop/gas, plus a pit delta and a
    flow delta) computed from data/synthetic/logs/<well_id>.parquet over the interval's
    own depth range
  - one 0/1 lookahead label per primary hazard: 1 if a *ground-truth* event of that type
    lands at MD in [top_md_m, top_md_m + lookahead_intervals*interval_m) — i.e. the label
    is "positive" for every interval that is within lookahead range of the event, not just
    the interval the event itself falls in. See tests/test_predict.py for the worked
    example (event at 2420 m, interval_m=50, lookahead_intervals=4).

Cached to models/features.parquet (`build_all(..., cache=True)` is the entry point; pass
`force=True` to rebuild from scratch after the DB has grown from more ingestion).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from nwis import db
from nwis.config import settings
from nwis.geo.correlate import formation_offset
from nwis.geo.nearby import nearby_wells, OffsetCandidate
from nwis.schema import EventType

try:
    from nwis.synth.hazards import MUD_WEIGHT_WINDOW_PPG
except ImportError:  # pragma: no cover - degrade honestly per the task brief
    MUD_WEIGHT_WINDOW_PPG = None

ALL_HAZARDS: list[str] = [e.value for e in EventType]
PRIMARY_HAZARDS: list[str] = ["stuck_pipe", "mud_loss", "kick", "overpressure"]

FEATURES_PATH: Path = settings.models_dir / "features.parquet"

LOG_PARAMS: list[str] = ["torque_kftlb", "spp_psi", "rop_m_hr", "gas_pct"]
LOG_PARAM_SHORT = {"torque_kftlb": "torque", "spp_psi": "spp", "rop_m_hr": "rop", "gas_pct": "gas"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _well_event_rows(well_id: str) -> list[dict]:
    """Every DrillingEvent on this well (truth + extracted) reduced to the fields the
    precedent-density and label logic need. `weight` = 1.0 for ground truth, else the
    extraction confidence (the honest way to let a shakier LLM-extracted event count for
    less than a ground-truth one)."""
    rows = []
    for e in db.events_for([well_id]):
        formation = e.formation
        offset = e.formation_offset_from_top_m
        if offset is None and formation is not None and e.depth_md_m is not None:
            _, offset = formation_offset(well_id, e.depth_md_m)
        rows.append({
            "event_type": e.event_type,
            "depth_md_m": e.depth_md_m,
            "formation": formation,
            "offset_from_top_m": offset,
            "hours_lost_npt": e.hours_lost_npt or 0.0,
            "is_truth": e.extraction_method == "synthetic_truth",
            "weight": 1.0 if e.extraction_method == "synthetic_truth" else float(e.extraction_confidence),
        })
    return rows


def _load_log(well_id: str) -> Optional[pd.DataFrame]:
    path = settings.synthetic_dir / "logs" / f"{well_id}.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - degrade honestly, matching nwis.geo.nearby
        return None


def _slope(depth: np.ndarray, y: np.ndarray) -> float:
    """OLS slope of y against depth over the window; 0.0 if <2 points or degenerate."""
    if len(depth) < 2 or float(depth.max() - depth.min()) < 1e-9:
        return 0.0
    A = np.column_stack([depth, np.ones_like(depth)])
    try:
        coeffs, *_ = np.linalg.lstsq(A, y, rcond=None)
        return float(coeffs[0])
    except np.linalg.LinAlgError:
        return 0.0


_EMPTY_LOG_STATS = {}
for _p in LOG_PARAMS:
    _s = LOG_PARAM_SHORT[_p]
    _EMPTY_LOG_STATS[f"{_s}_mean"] = np.nan
    _EMPTY_LOG_STATS[f"{_s}_max"] = np.nan
    _EMPTY_LOG_STATS[f"{_s}_slope"] = np.nan
_EMPTY_LOG_STATS["pit_delta"] = np.nan
_EMPTY_LOG_STATS["flow_delta"] = np.nan
_EMPTY_LOG_STATS["mw_actual_mean"] = np.nan


def _log_stats(log_df: Optional[pd.DataFrame], top: float, base: float) -> dict:
    """mean/max/slope per LOG_PARAMS + pit_delta + flow_delta + mw_actual_mean over
    [top, base). NaN when there's no log or no samples in range (skipped honestly, not
    zero-filled -- the model sees them as missing)."""
    if log_df is None or log_df.empty:
        return dict(_EMPTY_LOG_STATS)

    win = log_df[(log_df["depth_md_m"] >= top) & (log_df["depth_md_m"] < base)]
    if win.empty:
        return dict(_EMPTY_LOG_STATS)

    out: dict = {}
    depth = win["depth_md_m"].to_numpy(dtype=float)
    for p in LOG_PARAMS:
        short = LOG_PARAM_SHORT[p]
        y = win[p].to_numpy(dtype=float)
        out[f"{short}_mean"] = float(np.mean(y))
        out[f"{short}_max"] = float(np.max(y))
        out[f"{short}_slope"] = _slope(depth, y)

    pit = win["pit_vol_bbl"].to_numpy(dtype=float)
    out["pit_delta"] = float(pit[-1] - pit[0]) if len(pit) >= 2 else 0.0
    flow_diff = (win["flow_out_gpm"] - win["flow_in_gpm"]).to_numpy(dtype=float)
    out["flow_delta"] = float(np.mean(flow_diff))
    out["mw_actual_mean"] = float(win["mw_ppg"].mean())
    return out


def _mw_window_features(formation: Optional[str], mw_actual_mean: Optional[float]) -> dict:
    """planned-MW-vs-window features, skipped honestly if hazards.py or the formation's
    window is unavailable, or there's no log-derived actual MW for the interval."""
    keys = ["mw_pore_lo", "mw_pore_hi", "mw_frac_lo", "mw_frac_hi", "mw_margin_to_pore", "mw_margin_to_frac"]
    have_window = MUD_WEIGHT_WINDOW_PPG is not None and formation in (MUD_WEIGHT_WINDOW_PPG or {})
    have_actual = mw_actual_mean is not None and not (isinstance(mw_actual_mean, float) and math.isnan(mw_actual_mean))
    if not (have_window and have_actual):
        return {k: np.nan for k in keys}
    pore_lo, pore_hi, frac_lo, frac_hi = MUD_WEIGHT_WINDOW_PPG[formation]
    return {
        "mw_pore_lo": pore_lo, "mw_pore_hi": pore_hi, "mw_frac_lo": frac_lo, "mw_frac_hi": frac_hi,
        "mw_margin_to_pore": mw_actual_mean - pore_lo,
        "mw_margin_to_frac": frac_hi - mw_actual_mean,
    }


def _precedent_density(
    formation: Optional[str], band_lo: float, band_hi: float,
    candidates: list[OffsetCandidate], candidate_events: dict[str, list[dict]],
) -> dict:
    """Similarity-weighted offset-event density per hazard: formation-matched, and
    offset-matched against [band_lo, band_hi) in offset-from-top space (formation-
    normalised, so wells with different absolute depths line up on the same stage of the
    same formation). `candidates` is nearby_wells()'s output; `candidate_events` maps
    well_id -> _well_event_rows(well_id)."""
    counts = {h: 0.0 for h in ALL_HAZARDS}
    hours = {h: 0.0 for h in ALL_HAZARDS}
    simw = {h: 0.0 for h in ALL_HAZARDS}

    if formation is not None and band_hi > band_lo:
        for cand in candidates:
            sim = cand.score
            for ev in candidate_events.get(cand.well_id, []):
                if ev["formation"] != formation or ev["offset_from_top_m"] is None:
                    continue
                if not (band_lo <= ev["offset_from_top_m"] < band_hi):
                    continue
                hv = ev["event_type"].value if hasattr(ev["event_type"], "value") else str(ev["event_type"])
                counts[hv] += ev["weight"]
                hours[hv] += ev["hours_lost_npt"] * ev["weight"]
                simw[hv] += ev["weight"] * sim

    out = {}
    for h in ALL_HAZARDS:
        out[f"precedent_count_{h}"] = counts[h]
        out[f"precedent_hours_{h}"] = hours[h]
        out[f"precedent_simw_{h}"] = simw[h]
    return out


def is_lookahead_positive(interval_top_m: float, lookahead_m: float, event_depth_m: float) -> bool:
    """True if an event at `event_depth_m` lands within [interval_top_m, interval_top_m +
    lookahead_m) -- i.e. this interval is within lookahead range of (at or before) the
    event. Pulled out as its own function so the label logic has one place to be tested
    (see tests/test_predict.py's worked example: event at 2420 m, interval_m=50,
    lookahead_intervals=4 -> positive for interval tops 2250..2400, not 2450+)."""
    return interval_top_m <= event_depth_m < interval_top_m + lookahead_m


def build_features_for_well(well_id: str, radius_km: float = 3.0, offset_tol_m: Optional[float] = None) -> pd.DataFrame:
    """Feature rows for one well, surface to TD, in `settings.interval_m` steps.

    `offset_tol_m` defaults to `settings.interval_m` — an offset event from an offset well
    "counts" toward this interval if it fell within one interval-width of the same stage
    of the same formation (offset-from-top +/- half a step).
    """
    step = settings.interval_m
    lookahead_m = settings.lookahead_intervals * step
    offset_tol = offset_tol_m if offset_tol_m is not None else step

    well = db.get_well(well_id)
    if well is None:
        return pd.DataFrame()
    tops = db.tops_for(well_id)
    thickness_by_formation = {
        t.formation: ((t.base_md_m if t.base_md_m is not None else float("inf")) - t.top_md_m)
        for t in tops
    }

    log_df = _load_log(well_id)
    candidates = nearby_wells(well_id, radius_km)
    candidate_events = {c.well_id: _well_event_rows(c.well_id) for c in candidates}

    own_truth_events = [
        e for e in _well_event_rows(well_id) if e["is_truth"] and e["depth_md_m"] is not None
    ]

    n_intervals = max(1, math.ceil(well.td_md_m / step))
    rows = []
    for i in range(n_intervals):
        top = i * step
        base = top + step
        mid = (top + base) / 2.0

        formation, offset = formation_offset(well_id, mid)
        thickness = thickness_by_formation.get(formation) if formation else None
        fraction = (
            offset / thickness
            if (offset is not None and thickness and thickness > 0 and math.isfinite(thickness))
            else np.nan
        )

        log_stats = _log_stats(log_df, top, base)
        mw_actual_mean = log_stats.pop("mw_actual_mean")
        mw_feats = _mw_window_features(formation, mw_actual_mean)

        if offset is not None:
            band_lo, band_hi = offset - offset_tol / 2.0, offset + offset_tol / 2.0
        else:
            band_lo, band_hi = 0.0, -1.0  # empty band -> no matches
        precedent_feats = _precedent_density(formation, band_lo, band_hi, candidates, candidate_events)

        row = {
            "well_id": well_id,
            "top_md_m": float(top),
            "base_md_m": float(base),
            "mid_md_m": float(mid),
            "formation": formation,
            "offset_from_top_m": offset,
            "fraction_of_formation": fraction,
            "mw_actual_mean": mw_actual_mean,
            **mw_feats,
            **precedent_feats,
            **log_stats,
        }

        for h in PRIMARY_HAZARDS:
            label = 0
            for e in own_truth_events:
                ev_type = e["event_type"].value if hasattr(e["event_type"], "value") else str(e["event_type"])
                if ev_type != h:
                    continue
                d = e["depth_md_m"]
                if is_lookahead_positive(top, lookahead_m, d):
                    label = 1
                    break
            row[f"label_{h}"] = label

        rows.append(row)

    return pd.DataFrame(rows)


def build_all(well_ids: Optional[list[str]] = None, radius_km: float = 3.0, cache: bool = True, force: bool = False) -> pd.DataFrame:
    """Feature table for every well (or `well_ids`), cached to models/features.parquet."""
    if cache and not force and well_ids is None and FEATURES_PATH.exists():
        return pd.read_parquet(FEATURES_PATH)

    ids = well_ids if well_ids is not None else [w.well_id for w in db.all_wells()]
    frames = [build_features_for_well(wid, radius_km=radius_km) for wid in ids]
    frames = [f for f in frames if not f.empty]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if cache and well_ids is None and not df.empty:
        FEATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(FEATURES_PATH)
    return df


if __name__ == "__main__":  # pragma: no cover
    out = build_all(force=True)
    print(f"built features: {out.shape[0]} rows x {out.shape[1]} cols -> {FEATURES_PATH}")
    for h in PRIMARY_HAZARDS:
        print(h, "positives:", int(out[f"label_{h}"].sum()))
