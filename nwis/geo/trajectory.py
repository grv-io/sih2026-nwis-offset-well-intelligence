"""Minimum-curvature directional survey math (own implementation, no wellpathpy —
see docs/IMPLEMENTATION_PLAN.md ground rules) plus MD<->TVD lookups backed by
`nwis.db.surveys_for`.

This module is independent of `nwis.ingest.normalise` (which has its own
lightweight md_to_tvd for the ingestion path) so Phase 2/3 can evolve without
coupling; the underlying maths is the same textbook method.

All angles in degrees on the public API; radians internally.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd

from nwis import db

Station = tuple[float, float, float]  # (md, inc_deg, azi_deg)


def minimum_curvature(stations: Sequence[Station]) -> pd.DataFrame:
    """Classic minimum-curvature method.

    `stations` is a sequence of (md, inc_deg, azi_deg) sorted by md, station 0
    assumed at surface (md need not be exactly 0; tvd/north/east start at 0
    at the first station given).

    Returns a DataFrame(md, inc, azi, tvd, north, east, dls) where `dls` is the
    dogleg severity in degrees per 30 m (the convention used by nwis.synth).
    """
    if not stations:
        return pd.DataFrame(columns=["md", "inc", "azi", "tvd", "north", "east", "dls"])

    pts = sorted(stations, key=lambda t: t[0])
    md = np.array([p[0] for p in pts], dtype=float)
    inc = np.radians(np.array([p[1] for p in pts], dtype=float))
    azi = np.radians(np.array([p[2] for p in pts], dtype=float))

    n = len(md)
    tvd = np.zeros(n)
    north = np.zeros(n)
    east = np.zeros(n)
    dls = np.zeros(n)

    for i in range(1, n):
        d_md = md[i] - md[i - 1]
        i1, i2 = inc[i - 1], inc[i]
        a1, a2 = azi[i - 1], azi[i]

        cos_dl = math.cos(i2 - i1) - math.sin(i1) * math.sin(i2) * (1 - math.cos(a2 - a1))
        cos_dl = max(-1.0, min(1.0, cos_dl))
        dl = math.acos(cos_dl)  # dogleg angle (radians) over this course length

        rf = 1.0 if dl < 1e-9 else (2.0 / dl) * math.tan(dl / 2.0)

        d_tvd = (d_md / 2.0) * (math.cos(i1) + math.cos(i2)) * rf
        d_n = (d_md / 2.0) * (math.sin(i1) * math.cos(a1) + math.sin(i2) * math.cos(a2)) * rf
        d_e = (d_md / 2.0) * (math.sin(i1) * math.sin(a1) + math.sin(i2) * math.sin(a2)) * rf

        tvd[i] = tvd[i - 1] + d_tvd
        north[i] = north[i - 1] + d_n
        east[i] = east[i - 1] + d_e
        dls[i] = math.degrees(dl) * (30.0 / d_md) if d_md > 1e-9 else 0.0

    return pd.DataFrame({
        "md": md,
        "inc": np.degrees(inc),
        "azi": np.degrees(azi),
        "tvd": tvd,
        "north": north,
        "east": east,
        "dls": dls,
    })


def _stations_for_well(well_id: str) -> list[Station]:
    rows = db.surveys_for(well_id)
    pts = [(s.md_m, s.inc_deg, s.azi_deg) for s in rows]
    pts.sort(key=lambda t: t[0])
    if pts and pts[0][0] > 0:
        pts.insert(0, (0.0, 0.0, 0.0))
    return pts


def trajectory_table(well_id: str) -> pd.DataFrame | None:
    """Full minimum-curvature table for a well, or None if it has no surveys
    (callers should treat that as "assume vertical", i.e. tvd == md)."""
    pts = _stations_for_well(well_id)
    if not pts:
        return None
    return minimum_curvature(pts)


def md_to_tvd(well_id: str, md: float) -> float:
    """TVD at a given MD, interpolated from this well's surveys.

    Vertical fallback: if the well has no surveys at all, TVD == MD.
    Below the last station the last inclination/azimuth is implicitly held
    (matches the plan-truncation convention used by nwis.synth.trajectory).
    """
    table = trajectory_table(well_id)
    if table is None:
        return float(md)
    if md <= table["md"].iloc[0]:
        return float(md)
    if md <= table["md"].iloc[-1]:
        return float(np.interp(md, table["md"], table["tvd"]))
    # extend holding the last station's inclination/azimuth
    last = table.iloc[-1]
    extra = minimum_curvature([(last["md"], last["inc"], last["azi"]), (md, last["inc"], last["azi"])])
    return float(last["tvd"] + extra["tvd"].iloc[-1])


def tvd_to_md(well_id: str, tvd: float) -> float:
    """Inverse of md_to_tvd. Requires TVD to be monotonic in MD (true whenever
    inclination stays < 90 deg, which holds for every well in this project —
    no horizontals per nwis.schema.TrajectoryType usage in synth)."""
    table = trajectory_table(well_id)
    if table is None:
        return float(tvd)
    if tvd <= table["tvd"].iloc[0]:
        return float(tvd)
    if tvd <= table["tvd"].iloc[-1]:
        return float(np.interp(tvd, table["tvd"], table["md"]))
    last = table.iloc[-1]
    # vertical extrapolation beyond the last known station
    return float(last["md"] + (tvd - last["tvd"]) / max(math.cos(math.radians(last["inc"])), 1e-6))
