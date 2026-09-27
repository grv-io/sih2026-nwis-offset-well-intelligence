"""Azimuth-aware structural dip correction.

A competitor teardown (research/06 — CB-acc-tech "RigMind-NWIS") accepts a dip
azimuth from the user but never actually uses it in the depth-prediction math
(dip *direction* is decorative). We do it properly: fit a plane to the
formation-top TVDs of the field and use both dip magnitude and azimuth when
projecting a top from an offset well onto the active well.

Plane model
-----------
For a formation F, model top TVD as a plane over local ENU coordinates:

    tvd(east, north) = tvd0 + tan(theta) * (east * sin(phi) + north * cos(phi))

where `theta` is the regional dip angle and `phi` is the dip *azimuth*
(bearing, degrees from north, of the direction in which the formation gets
deeper — i.e. the down-dip direction). East/north are local flat-earth
coordinates (kilometres) about the mean position of the wells used in the fit
— fine at the ~50 km scale of the Upper Assam field, no map projection needed.

Sign convention for `predict_top_at`
-------------------------------------
Given offset well B's top TVD for formation F, the expected top at active
well A is:

    tvd_expected(A) = top_tvd(B) + tan(theta) * d * cos(bearing(B -> A) - phi)

Note the bearing term is **B -> A** (from the offset well to the active
well), not A -> B. This falls straight out of the plane model above: moving
from B to A shifts the horizontal position by d in the direction
bearing(B->A), so the TVD changes by tan(theta) * d * cos(bearing(B->A) - phi)
(projecting the B->A displacement onto the down-dip direction phi). Using the
A->B bearing instead would need the opposite sign — a mistake we specifically
avoid because it is the mistake that makes a dip azimuth term decorative
instead of load-bearing.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from nwis import db
from nwis.geo.distance import bearing_deg, haversine_km

# Fallback used when fewer than 3 wells carry the formation (can't fit a plane
# reliably). Kept local to nwis.geo — nwis.config is frozen/owned elsewhere.
DEFAULT_DIP_THETA_DEG = 2.0
DEFAULT_DIP_AZIMUTH_DEG = 45.0
MIN_WELLS_FOR_FIT = 3


def _formation_tops(formation: str) -> list[tuple[str, float, float, float]]:
    """(well_id, lat, lon, top_tvd_m) for every well that has this formation topped."""
    out = []
    for w in db.all_wells():
        for t in db.tops_for(w.well_id):
            if t.formation == formation:
                out.append((w.well_id, w.lat, w.lon, t.top_tvd_m))
                break
    return out


def fit_dip(formation: str) -> tuple[float, float, float, int]:
    """Fit regional dip angle + azimuth to `formation`'s top TVD across all
    wells that penetrate it, by least-squares plane fit.

    Returns (theta_deg, azimuth_deg, rmse_m, n). Falls back to
    (DEFAULT_DIP_THETA_DEG, DEFAULT_DIP_AZIMUTH_DEG, nan, n) when n < 3.
    """
    rows = _formation_tops(formation)
    n = len(rows)
    if n < MIN_WELLS_FOR_FIT:
        return DEFAULT_DIP_THETA_DEG, DEFAULT_DIP_AZIMUTH_DEG, float("nan"), n

    lat0 = sum(r[1] for r in rows) / n
    lon0 = sum(r[2] for r in rows) / n
    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(math.radians(lat0))

    east = np.array([(r[2] - lon0) * km_per_deg_lon for r in rows])
    north = np.array([(r[1] - lat0) * km_per_deg_lat for r in rows])
    tvd = np.array([r[3] for r in rows])

    # Least squares: tvd = a + b*east + c*north
    X = np.column_stack([np.ones(n), east, north])
    coeffs, *_ = np.linalg.lstsq(X, tvd, rcond=None)
    a, b, c = coeffs

    grad_km = math.hypot(b, c)  # m of TVD per km horizontal, along steepest direction
    theta_deg = math.degrees(math.atan(grad_km / 1000.0))  # m/m -> angle
    azimuth_deg = (math.degrees(math.atan2(b, c)) + 360.0) % 360.0  # atan2(east_comp, north_comp)

    pred = X @ coeffs
    rmse = float(np.sqrt(np.mean((pred - tvd) ** 2)))
    return theta_deg, azimuth_deg, rmse, n


def predict_top_at(active_well_id: str, formation: str) -> tuple[Optional[float], int, str]:
    """Expected TVD of `formation`'s top at `active_well_id`, dip-corrected
    from every other well that has that top, per the sign convention above.

    Returns (tvd_expected_m, n_wells_used, method). `method` is
    "dip_projection_fit" when >= MIN_WELLS_FOR_FIT wells informed theta/phi,
    "dip_projection_default" when the config fallback was used, or
    "no_offset_data" (tvd None, n 0) if no other well has this top at all.
    """
    theta_deg, phi_deg, _rmse, n_fit = fit_dip(formation)
    method = "dip_projection_fit" if n_fit >= MIN_WELLS_FOR_FIT else "dip_projection_default"

    A = db.get_well(active_well_id)
    if A is None:
        return None, 0, "unknown_well"

    preds = []
    for well_id, lat_b, lon_b, top_tvd_b in _formation_tops(formation):
        if well_id == active_well_id:
            continue
        d_km = haversine_km(lat_b, lon_b, A.lat, A.lon)
        bearing_b_to_a = bearing_deg(lat_b, lon_b, A.lat, A.lon)
        pred = top_tvd_b + math.tan(math.radians(theta_deg)) * (d_km * 1000.0) * math.cos(
            math.radians(bearing_b_to_a - phi_deg)
        )
        preds.append(pred)

    if not preds:
        return None, 0, "no_offset_data"
    return float(np.mean(preds)), len(preds), method
