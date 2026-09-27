"""Basin model for the synthetic Duliajan-Moran-Naharkatiya area.

Formation tops = regional surface (gentle NE dip + a central basement high near
Dibrugarh-Tinsukia) + a smooth per-formation thickness field + small per-well noise.
Thickness ranges and formation order per research/03_public_datasets.md and nwis.schema.FORMATIONS.

ILLUSTRATIVE / SYNTHETIC data — see data/synthetic/README.md.
"""
from __future__ import annotations

import hashlib

import numpy as np

from nwis.schema import FORMATIONS

# Bounding box used across nwis/synth (Duliajan-Moran-Naharkatiya area).
LAT_MIN, LAT_MAX = 27.25, 27.55
LON_MIN, LON_MAX = 94.95, 95.45

# Central basement high, "near Dibrugarh-Tinsukia" per research/03 structural note.
BASEMENT_HIGH_LAT, BASEMENT_HIGH_LON = 27.47, 95.18
BASEMENT_HIGH_SIGMA_DEG = 0.10
BASEMENT_HIGH_UPLIFT_M = 350.0  # shallowing amplitude at the high's centre

# Gentle regional dip: basin deepens to the NE (away from the SW corner reference point).
DIP_RATE_LAT_M_PER_DEG = 400.0
DIP_RATE_LON_M_PER_DEG = 250.0

TD_MID_M = 3350.0  # centre of the requested 2,500-4,200 m TD band
TD_HALF_RANGE_M = 850.0

NON_BASEMENT_FORMATIONS = [f for f in FORMATIONS if f != "Basement"]

# (min, max) typical thickness in metres, from research/03 table.
THICKNESS_RANGE_M: dict[str, tuple[float, float]] = {
    "Alluvium": (20.0, 60.0),
    "Dhekiajuli": (150.0, 350.0),
    "Namsang": (200.0, 450.0),
    "Girujan": (700.0, 1300.0),
    "Tipam": (600.0, 1200.0),
    "Barail": (800.0, 1500.0),
    "Kopili": (200.0, 500.0),
    "Sylhet": (200.0, 400.0),
}

BASEMENT_PENETRATION_RANGE_M = (20.0, 120.0)


def _stable_unit(*parts: object) -> float:
    """Deterministic pseudo-random value in [0, 1) derived from `parts` (no run-to-run drift)."""
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _norm(v: float, lo: float, hi: float) -> float:
    return (v - lo) / (hi - lo)


def _smooth_field(lat: float, lon: float, formation: str) -> float:
    """Smooth, spatially-continuous value in ~[-1, 1] for one formation.

    Two low-frequency sinusoids with formation-specific (but fixed) frequency/phase so the
    field is smooth across the whole basin box: neighbouring wells get similar values,
    distant wells can differ a lot.
    """
    x = _norm(lon, LON_MIN, LON_MAX)
    y = _norm(lat, LAT_MIN, LAT_MAX)
    total = 0.0
    for k in range(2):
        kx = 1.0 + 2.0 * _stable_unit(formation, "kx", k)
        ky = 1.0 + 2.0 * _stable_unit(formation, "ky", k)
        phase = 2 * np.pi * _stable_unit(formation, "phase", k)
        amp = 1.0 / (k + 1)
        total += amp * np.sin(2 * np.pi * (kx * x + ky * y) + phase)
    max_total = sum(1.0 / (k + 1) for k in range(2))
    return float(np.clip(total / max_total, -1.0, 1.0))


def basement_high_factor(lat: float, lon: float) -> float:
    """1.0 far from the high, ->0 at its centre (used to shallow structure there)."""
    d2 = (lat - BASEMENT_HIGH_LAT) ** 2 + (lon - BASEMENT_HIGH_LON) ** 2
    return float(np.exp(-d2 / (2 * BASEMENT_HIGH_SIGMA_DEG ** 2)))


def target_td_m(lat: float, lon: float, rng: np.random.Generator) -> float:
    """Structural target TD (a TVD): regional dip deepens NE, basement high shallows locally."""
    dip = (DIP_RATE_LAT_M_PER_DEG * (lat - (LAT_MIN + LAT_MAX) / 2) / (LAT_MAX - LAT_MIN)
           + DIP_RATE_LON_M_PER_DEG * (lon - (LON_MIN + LON_MAX) / 2) / (LON_MAX - LON_MIN))
    uplift = -BASEMENT_HIGH_UPLIFT_M * basement_high_factor(lat, lon)
    base = TD_MID_M + dip + uplift + rng.normal(0.0, 100.0)
    return float(np.clip(base, 2500.0, 4200.0))


def tops_and_td_for_location(lat: float, lon: float, rng: np.random.Generator
                              ) -> tuple[list[tuple[str, float]], float]:
    """Full stratigraphy for one well location: list of (formation, top_tvd_m) + TD (TVD)."""
    target_td = target_td_m(lat, lon, rng)

    thicknesses: dict[str, float] = {}
    for f in NON_BASEMENT_FORMATIONS:
        lo, hi = THICKNESS_RANGE_M[f]
        mid, half = (lo + hi) / 2.0, (hi - lo) / 2.0
        field_val = _smooth_field(lat, lon, f)
        noise = rng.normal(0.0, 0.06)
        frac = float(np.clip(field_val + noise, -1.0, 1.0))
        thicknesses[f] = mid + half * frac

    raw_total = sum(thicknesses.values())
    basement_pen = rng.uniform(*BASEMENT_PENETRATION_RANGE_M)
    scale = float(np.clip((target_td - basement_pen) / raw_total, 0.45, 1.35))

    tops: list[tuple[str, float]] = []
    depth = 0.0
    for f in NON_BASEMENT_FORMATIONS:
        tops.append((f, depth))
        depth += thicknesses[f] * scale
    tops.append(("Basement", depth))
    td = depth + basement_pen
    return tops, td


def tops_for_location(lat: float, lon: float, rng: np.random.Generator) -> list[tuple[str, float]]:
    """Required public signature: list of (formation, top_tvd) for this well location."""
    tops, _td = tops_and_td_for_location(lat, lon, rng)
    return tops
