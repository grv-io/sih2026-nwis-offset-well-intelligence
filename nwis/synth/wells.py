"""30 synthetic wells across 3 Upper Assam fields (Duliajan, Moran, Naharkatiya).

ILLUSTRATIVE / SYNTHETIC. Field names/approximate locations per research/01 (OIL's Upper
Assam core operating area). Trajectories: ~70% vertical, ~30% deviated S-shaped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from nwis.schema import TrajectoryType, Well
from nwis.synth import stratigraphy
from nwis.synth.trajectory import TrajectoryTable, trajectory_for_target_tvd

FIELD_CENTERS: dict[str, tuple[float, float]] = {
    "Duliajan": (27.48, 95.32),
    "Moran": (27.30, 95.03),
    "Naharkatiya": (27.36, 95.20),
}
FIELD_WELL_COUNTS: dict[str, int] = {"Duliajan": 12, "Moran": 10, "Naharkatiya": 8}
FIELD_PREFIX: dict[str, str] = {"Duliajan": "DUL", "Moran": "MOR", "Naharkatiya": "NKT"}

SPUD_YEAR_RANGE = (2008, 2025)
DEVIATED_FRACTION = 0.30
CLUSTER_SPREAD_DEG = 0.045


@dataclass
class SynthWell:
    """A generated well plus everything derived from its location."""
    well: Well
    tops_tvd: list[tuple[str, float]]  # (formation, top_tvd_m)
    tops_md: list[tuple[str, float]]   # (formation, top_md_m)
    trajectory: TrajectoryTable


def _clip_to_box(lat: float, lon: float) -> tuple[float, float]:
    lat = float(np.clip(lat, stratigraphy.LAT_MIN + 0.01, stratigraphy.LAT_MAX - 0.01))
    lon = float(np.clip(lon, stratigraphy.LON_MIN + 0.01, stratigraphy.LON_MAX - 0.01))
    return lat, lon


def _spud_date(rng: np.random.Generator) -> date:
    year = int(rng.integers(SPUD_YEAR_RANGE[0], SPUD_YEAR_RANGE[1] + 1))
    day_of_year = int(rng.integers(1, 366))
    return date(year, 1, 1) + timedelta(days=day_of_year - 1)


def generate_wells(rng: np.random.Generator) -> list[SynthWell]:
    out: list[SynthWell] = []
    for field, count in FIELD_WELL_COUNTS.items():
        center_lat, center_lon = FIELD_CENTERS[field]
        prefix = FIELD_PREFIX[field]
        for i in range(1, count + 1):
            well_id = f"{prefix}-{i:03d}"
            lat, lon = _clip_to_box(
                center_lat + rng.normal(0.0, CLUSTER_SPREAD_DEG),
                center_lon + rng.normal(0.0, CLUSTER_SPREAD_DEG),
            )
            tops_tvd, td_tvd = stratigraphy.tops_and_td_for_location(lat, lon, rng)

            traj_type = TrajectoryType.deviated if rng.random() < DEVIATED_FRACTION else TrajectoryType.vertical
            trajectory = trajectory_for_target_tvd(td_tvd, traj_type.value, rng)
            td_md = float(trajectory.md[-1])

            tops_md = [(f, float(trajectory.md_at_tvd(tvd))) for f, tvd in tops_tvd]

            well = Well(
                well_id=well_id,
                name=f"{field} {i}",
                field=field,
                lat=lat,
                lon=lon,
                kb_elev_m=float(rng.uniform(90.0, 130.0)),
                spud_date=_spud_date(rng),
                td_md_m=td_md,
                trajectory_type=traj_type,
                status="completed",
                basin="Upper Assam",
            )
            out.append(SynthWell(well=well, tops_tvd=tops_tvd, tops_md=tops_md, trajectory=trajectory))
    return out
