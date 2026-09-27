"""Per-formation hazard model: rates, hot zones, mud-weight windows, ROP baselines.

ILLUSTRATIVE / SYNTHETIC — ties nwis.schema.EventType occurrences to formations per the
Upper Assam hazard notes in research/03_public_datasets.md:
  Girujan  -> stuck_pipe / wellbore_instability (sticky clay)
  Tipam    -> mud_loss / gas_show (permeable sand)
  Barail   -> overpressure / kick / mud_loss (sand-coal contacts)
  Kopili   -> kick / overpressure
  Sylhet   -> mud_loss (fractured carbonate)
  Basement contact -> torque_spike
  Casing points    -> cementing_issue
  Severe stuck_pipe -> sometimes followed by fishing_operation
"""
from __future__ import annotations

from nwis.schema import EventType as ET

# --------------------------------------------------------------------------- #
# Base hazard weights: formation -> {event_type: weight}. Weight is relative,
# not a probability; normalised at sampling time in events.py.
# --------------------------------------------------------------------------- #
FORMATION_HAZARD_WEIGHTS: dict[str, dict[ET, float]] = {
    "Alluvium": {ET.wellbore_instability: 0.6, ET.npt_other: 0.3},
    "Dhekiajuli": {ET.wellbore_instability: 0.5, ET.gas_show: 0.3, ET.npt_other: 0.2},
    "Namsang": {ET.wellbore_instability: 0.8, ET.npt_other: 0.3},
    "Girujan": {ET.stuck_pipe: 2.5, ET.wellbore_instability: 1.8, ET.torque_spike: 0.4},
    "Tipam": {ET.mud_loss: 2.2, ET.gas_show: 1.2, ET.kick: 0.4},
    "Barail": {ET.overpressure: 1.6, ET.kick: 1.4, ET.mud_loss: 1.2, ET.stuck_pipe: 0.5},
    "Kopili": {ET.kick: 1.8, ET.overpressure: 1.5, ET.wellbore_instability: 0.5},
    "Sylhet": {ET.mud_loss: 1.8, ET.overpressure: 0.6},
    "Basement": {ET.torque_spike: 2.0},
}

# Typical casing-shoe formations (top-of-formation is where a casing string is often set).
CASING_SHOE_FORMATIONS: list[str] = ["Namsang", "Girujan", "Tipam", "Barail"]
CEMENTING_ISSUE_PROB_PER_SHOE = 0.22

FISHING_AFTER_SEVERE_STUCK_PIPE_PROB = 0.30

# --------------------------------------------------------------------------- #
# Mud-weight window per formation: (pore_ppg_lo, pore_ppg_hi, frac_ppg_lo, frac_ppg_hi)
# --------------------------------------------------------------------------- #
MUD_WEIGHT_WINDOW_PPG: dict[str, tuple[float, float, float, float]] = {
    "Alluvium": (8.4, 8.8, 11.5, 12.5),
    "Dhekiajuli": (8.6, 9.0, 12.0, 13.0),
    "Namsang": (8.8, 9.3, 12.5, 13.5),
    "Girujan": (9.0, 9.8, 13.0, 14.0),
    "Tipam": (9.2, 10.0, 13.5, 14.5),
    "Barail": (10.0, 12.2, 14.0, 15.8),
    "Kopili": (10.5, 12.6, 14.5, 16.2),
    "Sylhet": (10.0, 11.6, 14.8, 16.0),
    "Basement": (10.5, 12.0, 15.5, 17.0),
}

# ROP baseline (m/hr) per formation: (low, high) — sand drills faster than clay/coal/carbonate.
ROP_BASELINE_M_HR: dict[str, tuple[float, float]] = {
    "Alluvium": (35.0, 55.0),
    "Dhekiajuli": (25.0, 40.0),
    "Namsang": (20.0, 35.0),
    "Girujan": (10.0, 20.0),
    "Tipam": (18.0, 32.0),
    "Barail": (6.0, 14.0),
    "Kopili": (8.0, 16.0),
    "Sylhet": (5.0, 12.0),
    "Basement": (2.0, 6.0),
}

# --------------------------------------------------------------------------- #
# Hot zones: spatially-clustered hazard clusters so nearby wells share problems.
# lat/lon centre, radius in km, formation, hazard, and a weight multiplier applied
# to that (formation, hazard) pair for any well whose location falls inside the radius.
# --------------------------------------------------------------------------- #
HOT_ZONES: list[dict] = [
    {"id": "HZ-DUL-GIRUJAN", "lat": 27.48, "lon": 95.32, "radius_km": 14.0,
     "formation": "Girujan", "hazard": ET.stuck_pipe, "multiplier": 4.0,
     "note": "Duliajan field — sticky Girujan clay, correlated differential-sticking history"},
    {"id": "HZ-MOR-TIPAM", "lat": 27.30, "lon": 95.03, "radius_km": 12.0,
     "formation": "Tipam", "hazard": ET.mud_loss, "multiplier": 4.0,
     "note": "Moran field — depleted/weak Tipam sand, recurring lost circulation"},
    {"id": "HZ-NKT-BARAIL", "lat": 27.36, "lon": 95.20, "radius_km": 12.0,
     "formation": "Barail", "hazard": ET.overpressure, "multiplier": 4.0,
     "note": "Naharkatiya field — Barail coal-shale abnormal geopressure belt"},
    {"id": "HZ-DUL-KOPILI", "lat": 27.50, "lon": 95.38, "radius_km": 8.0,
     "formation": "Kopili", "hazard": ET.kick, "multiplier": 3.5,
     "note": "NE Duliajan — deep Kopili overpressured shale, kick history"},
    {"id": "HZ-MOR-SYLHET", "lat": 27.27, "lon": 95.08, "radius_km": 8.0,
     "formation": "Sylhet", "hazard": ET.mud_loss, "multiplier": 3.5,
     "note": "SW Moran — fractured Sylhet carbonate, circulation losses"},
    {"id": "HZ-NKT-GIRUJAN", "lat": 27.33, "lon": 95.23, "radius_km": 8.0,
     "formation": "Girujan", "hazard": ET.wellbore_instability, "multiplier": 3.0,
     "note": "Naharkatiya — Girujan hole instability cluster"},
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, a ** 0.5))


def hot_zones_for_well(lat: float, lon: float) -> list[dict]:
    return [hz for hz in HOT_ZONES if haversine_km(lat, lon, hz["lat"], hz["lon"]) <= hz["radius_km"]]


def hazard_weight_table(lat: float, lon: float) -> dict[tuple[str, ET], float]:
    """(formation, event_type) -> weight for this well location, base rates boosted by hot zones."""
    table: dict[tuple[str, ET], float] = {}
    for formation, weights in FORMATION_HAZARD_WEIGHTS.items():
        for et, w in weights.items():
            table[(formation, et)] = w

    for hz in hot_zones_for_well(lat, lon):
        key = (hz["formation"], hz["hazard"])
        table[key] = table.get(key, 0.3) * hz["multiplier"]

    return table


def mud_weight_sample(formation: str, event_type: ET, rng) -> float:
    pore_lo, pore_hi, frac_lo, frac_hi = MUD_WEIGHT_WINDOW_PPG[formation]
    if event_type in (ET.mud_loss,):
        # Losses correlate with running close to / above the frac gradient.
        return float(rng.uniform(frac_lo - 0.3, frac_hi + 0.2))
    if event_type in (ET.kick, ET.overpressure):
        # Kicks correlate with mud weight under pore pressure equivalent.
        return float(rng.uniform(pore_lo - 0.4, pore_hi - 0.1))
    return float(rng.uniform(pore_hi, frac_lo))


def rop_baseline(formation: str, rng) -> float:
    lo, hi = ROP_BASELINE_M_HR[formation]
    return float(rng.uniform(lo, hi))
