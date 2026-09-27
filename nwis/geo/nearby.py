"""Offset-well similarity ranking with honest degradation (A1/A11 in
docs/IMPLEMENTATION_PLAN.md §6 — the Physics0070 pattern this project adopts
on purpose, unlike competitors that silently zero-fill missing dimensions).

Dimensions, each in [0, 1], higher = more similar:
  geographic        1 - distance/radius
  formation_overlap Jaccard of formations penetrated (from formation_tops)
  trajectory_type   1.0 if same TrajectoryType else 0.0
  target_depth      1 - |delta TD| / max(TD active, TD candidate)
  difficulty         TADI-style D = z(WOB) + z(Torque) - z(ROP), z-scored across
                     the active well + in-radius candidates that have a
                     data/synthetic/logs/<well>.parquet file (per candidate:
                     1 - |D_active - D_candidate| / 6, clamped to [0, 1])

When a dimension can't be computed for a candidate (most commonly: no parquet
log for one of the two wells), it is dropped for that candidate and the
remaining weights are renormalised to sum to 1 — never silently zero-filled.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from pydantic import BaseModel

from nwis import db
from nwis.config import settings
from nwis.geo.distance import bearing_deg, haversine_km

WEIGHTS: dict[str, float] = {
    "geographic": 0.35,
    "formation_overlap": 0.25,
    "trajectory_type": 0.15,
    "target_depth": 0.15,
    "difficulty": 0.10,
}

DIFFICULTY_Z_RANGE = 6.0  # heuristic span of z(WOB)+z(Torque)-z(ROP); clamps score to [0,1]


class OffsetCandidate(BaseModel):
    well_id: str
    score: float
    distance_km: float
    bearing_deg: float
    dimensions: dict[str, float]
    weights_used: dict[str, float]
    dimensions_unavailable: list[str] = []


def _formations_penetrated(well_id: str) -> set[str]:
    return {t.formation for t in db.tops_for(well_id)}


def _difficulty_raw(well_id: str) -> Optional[tuple[float, float, float]]:
    """(mean WOB, mean torque, mean ROP) from this well's parquet log, or None."""
    path = settings.synthetic_dir / "logs" / f"{well_id}.parquet"
    if not path.exists():
        return None
    import pandas as pd
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 — a corrupt/partial log degrades honestly too
        return None
    cols = {c.lower(): c for c in df.columns}

    def pick(*names: str):
        for nm in names:
            if nm in cols:
                return df[cols[nm]]
        return None

    wob, torque, rop = pick("wob_klbf", "wob"), pick("torque_kftlb", "torque"), pick("rop_m_hr", "rop")
    if wob is None or torque is None or rop is None or len(df) == 0:
        return None
    return float(wob.mean()), float(torque.mean()), float(rop.mean())


def _difficulty_index(well_ids: list[str]) -> dict[str, float]:
    """Z-scored D = z(WOB)+z(Torque)-z(ROP) across the given wells (those with logs)."""
    raw = {wid: v for wid in well_ids if (v := _difficulty_raw(wid)) is not None}
    if len(raw) < 2:
        return {}
    arr = np.array(list(raw.values()))  # (n, 3)

    def z(col: np.ndarray) -> np.ndarray:
        std = col.std()
        return (col - col.mean()) / std if std > 1e-9 else np.zeros_like(col)

    zw, zt, zr = z(arr[:, 0]), z(arr[:, 1]), z(arr[:, 2])
    d_vals = zw + zt - zr
    return {wid: float(d) for wid, d in zip(raw.keys(), d_vals)}


def nearby_wells(active_well_id: str, radius_km: float) -> list[OffsetCandidate]:
    """Wells within `radius_km` of `active_well_id`, ranked by similarity score."""
    A = db.get_well(active_well_id)
    if A is None:
        return []

    in_radius: list[tuple] = []
    for w in db.all_wells():
        if w.well_id == active_well_id:
            continue
        d_km = haversine_km(A.lat, A.lon, w.lat, w.lon)
        if d_km <= radius_km:
            in_radius.append((w, d_km))
    if not in_radius:
        return []

    formations_a = _formations_penetrated(active_well_id)
    difficulty = _difficulty_index([active_well_id] + [w.well_id for w, _ in in_radius])
    max_td = max([A.td_md_m] + [w.td_md_m for w, _ in in_radius]) or 1.0

    out: list[OffsetCandidate] = []
    for w, d_km in in_radius:
        dims: dict[str, float] = {}
        unavailable: list[str] = []

        dims["geographic"] = max(0.0, 1.0 - d_km / radius_km) if radius_km > 0 else 1.0

        formations_b = _formations_penetrated(w.well_id)
        union = formations_a | formations_b
        dims["formation_overlap"] = (len(formations_a & formations_b) / len(union)) if union else 0.0

        dims["trajectory_type"] = 1.0 if A.trajectory_type == w.trajectory_type else 0.0

        dims["target_depth"] = max(0.0, 1.0 - abs(A.td_md_m - w.td_md_m) / max_td)

        if active_well_id in difficulty and w.well_id in difficulty:
            dims["difficulty"] = max(
                0.0, 1.0 - abs(difficulty[active_well_id] - difficulty[w.well_id]) / DIFFICULTY_Z_RANGE
            )
        else:
            unavailable.append("difficulty")

        weights_used = {k: v for k, v in WEIGHTS.items() if k not in unavailable}
        wsum = sum(weights_used.values()) or 1.0
        weights_used = {k: v / wsum for k, v in weights_used.items()}

        score = sum(dims[k] * weights_used[k] for k in weights_used)

        out.append(OffsetCandidate(
            well_id=w.well_id,
            score=score,
            distance_km=d_km,
            bearing_deg=bearing_deg(A.lat, A.lon, w.lat, w.lon),
            dimensions=dims,
            weights_used=weights_used,
            dimensions_unavailable=unavailable,
        ))

    out.sort(key=lambda c: c.score, reverse=True)
    return out
