"""1 m-step drilling-parameter log per well, with formation baselines + event precursors.

Columns match nwis.schema.LiveSample (well_id, t_s, depth_md_m, wob_klbf, rpm, torque_kftlb,
rop_m_hr, spp_psi, flow_in_gpm, flow_out_gpm, pit_vol_bbl, mw_ppg, gas_pct).

Precursors before each ground-truth event (a real signal for the Phase-6 alert engine to
later detect, and what the extraction-eval / anomaly tests check):
  stuck_pipe / wellbore_instability -> torque + WOB ramp up, ROP ramp down, 50-85 m before
  mud_loss                          -> flow_out < flow_in, pit volume falling
  kick / gas_show                   -> gas rise, pit gain, flow_out > flow_in
  overpressure                      -> SPP ramp up
  torque_spike (basement contact)   -> short sharp torque spike

ILLUSTRATIVE / SYNTHETIC.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from nwis.schema import DrillingEvent, EventType as ET
from nwis.synth import hazards
from nwis.synth.wells import SynthWell

STEP_M = 1.0

BASELINE_BY_FORMATION: dict[str, dict[str, tuple[float, float]]] = {
    # formation -> {param: (low, high)} centre range, sampled once per well for continuity
    "Alluvium":   {"wob": (8, 14), "rpm": (100, 150), "torque": (3, 6), "flow_in": (500, 650), "gas": (0.1, 0.5)},
    "Dhekiajuli": {"wob": (10, 16), "rpm": (100, 150), "torque": (4, 7), "flow_in": (550, 700), "gas": (0.1, 0.6)},
    "Namsang":    {"wob": (12, 18), "rpm": (90, 140), "torque": (5, 9), "flow_in": (550, 700), "gas": (0.2, 0.6)},
    "Girujan":    {"wob": (14, 22), "rpm": (80, 130), "torque": (7, 13), "flow_in": (600, 750), "gas": (0.2, 0.7)},
    "Tipam":      {"wob": (16, 24), "rpm": (90, 140), "torque": (7, 12), "flow_in": (650, 800), "gas": (0.3, 1.0)},
    "Barail":     {"wob": (20, 30), "rpm": (70, 120), "torque": (10, 17), "flow_in": (650, 800), "gas": (0.4, 1.5)},
    "Kopili":     {"wob": (18, 28), "rpm": (70, 120), "torque": (9, 16), "flow_in": (600, 750), "gas": (0.4, 1.6)},
    "Sylhet":     {"wob": (20, 30), "rpm": (60, 110), "torque": (10, 17), "flow_in": (600, 750), "gas": (0.2, 0.8)},
    "Basement":   {"wob": (22, 34), "rpm": (50, 100), "torque": (11, 19), "flow_in": (600, 750), "gas": (0.1, 0.4)},
}


def _ar1(n: int, sigma: float, phi: float, rng: np.random.Generator) -> np.ndarray:
    if n <= 0:
        return np.zeros(0)
    innovations = rng.normal(0.0, sigma, size=n)
    return lfilter([1.0], [1.0, -phi], innovations)


def _formation_series(depths: np.ndarray, sw: SynthWell) -> np.ndarray:
    order = [f for f, _ in sw.tops_md]
    bounds = np.array([md for _, md in sw.tops_md] + [sw.trajectory.md[-1]])
    idx = np.searchsorted(bounds, depths, side="right") - 1
    idx = np.clip(idx, 0, len(order) - 1)
    return np.array(order)[idx]


def _ramp_window(depths: np.ndarray, center_depth: float, width_m: float) -> np.ndarray:
    """0 far away, ramps linearly to 1 exactly at center_depth over the preceding width_m."""
    start = center_depth - width_m
    r = (depths - start) / max(width_m, 1e-6)
    r = np.clip(r, 0.0, 1.0)
    r[depths > center_depth] = 0.0
    return r


def generate_log(sw: SynthWell, events: list[DrillingEvent], rng: np.random.Generator) -> pd.DataFrame:
    well = sw.well
    td = float(sw.trajectory.md[-1])
    depths = np.arange(0.0, td + STEP_M, STEP_M)
    n = len(depths)
    formations = _formation_series(depths, sw)

    wob = np.zeros(n)
    rpm = np.zeros(n)
    torque = np.zeros(n)
    flow_in = np.zeros(n)
    gas = np.zeros(n)

    for f, params in BASELINE_BY_FORMATION.items():
        mask = formations == f
        if not mask.any():
            continue
        for arr, key, floor in ((wob, "wob", 3.0), (rpm, "rpm", 40.0), (torque, "torque", 1.5),
                                 (flow_in, "flow_in", 300.0), (gas, "gas", 0.05)):
            lo, hi = params[key]
            arr[mask] = rng.uniform(lo, hi)

    # drag increases with depth; add smooth AR(1) noise for realistic wiggle
    torque = torque + 0.0025 * depths + _ar1(n, 0.4, 0.85, rng)
    wob = wob + _ar1(n, 0.5, 0.85, rng)
    rpm = rpm + _ar1(n, 3.0, 0.8, rng)
    flow_in = flow_in + _ar1(n, 8.0, 0.85, rng)
    gas = np.clip(gas + _ar1(n, 0.05, 0.7, rng), 0.02, None)

    rop = np.array([hazards.rop_baseline(f, rng) for f in formations])
    rop = np.clip(rop + _ar1(n, 1.0, 0.8, rng), 1.0, None)

    spp = 800.0 + 0.16 * depths + _ar1(n, 15.0, 0.85, rng)
    flow_out = flow_in.copy() + _ar1(n, 5.0, 0.7, rng)
    pit = 500.0 + _ar1(n, 1.5, 0.95, rng).cumsum() * 0.02  # slow drift baseline

    mw_center = np.array([
        (hazards.MUD_WEIGHT_WINDOW_PPG[f][0] + hazards.MUD_WEIGHT_WINDOW_PPG[f][1]) / 2.0
        for f in formations
    ])
    mw = mw_center + _ar1(n, 0.05, 0.9, rng)

    # --- precursors ---------------------------------------------------------
    for e in events:
        depth = e.depth_md_m
        if depth is None:
            continue
        if e.event_type in (ET.stuck_pipe, ET.wellbore_instability):
            # Width >=50 m guarantees the full 50 m pre-event window (used by the precursor
            # test / a live alert rule looking "50 m back") sits inside the ramp, not partly
            # in flat baseline.
            w = float(rng.uniform(50.0, 85.0))
            r = _ramp_window(depths, depth, w)
            torque *= (1.0 + r * rng.uniform(0.7, 1.6))
            wob *= (1.0 + r * rng.uniform(0.3, 0.6))
            rop *= (1.0 - r * rng.uniform(0.4, 0.7))
        elif e.event_type == ET.mud_loss:
            w = float(rng.uniform(30.0, 80.0))
            r = _ramp_window(depths, depth, w)
            flow_out -= r * rng.uniform(50.0, 200.0)
            pit -= r * rng.uniform(30.0, 150.0)
        elif e.event_type in (ET.kick, ET.gas_show):
            w = float(rng.uniform(30.0, 80.0))
            r = _ramp_window(depths, depth, w)
            gas += r * rng.uniform(2.0, 8.0)
            pit += r * rng.uniform(20.0, 100.0)
            flow_out += r * rng.uniform(30.0, 120.0)
        elif e.event_type == ET.overpressure:
            w = float(rng.uniform(30.0, 80.0))
            r = _ramp_window(depths, depth, w)
            spp += r * rng.uniform(150.0, 500.0)
        elif e.event_type == ET.torque_spike:
            w = float(rng.uniform(10.0, 25.0))
            r = _ramp_window(depths, depth, w)
            torque += r * rng.uniform(300.0, 800.0)

    flow_out = np.clip(flow_out, 50.0, None)
    pit = np.clip(pit, 50.0, None)
    torque = np.clip(torque, 0.5, None)
    wob = np.clip(wob, 1.0, None)
    rpm = np.clip(rpm, 10.0, None)
    spp = np.clip(spp, 100.0, None)
    mw = np.clip(mw, 7.5, 20.0)
    gas = np.clip(gas, 0.0, 100.0)

    dt_hr = STEP_M / np.clip(rop, 0.5, None)
    t_s = np.cumsum(dt_hr * 3600.0)

    df = pd.DataFrame({
        "well_id": well.well_id,
        "t_s": t_s,
        "depth_md_m": depths,
        "wob_klbf": wob,
        "rpm": rpm,
        "torque_kftlb": torque,
        "rop_m_hr": rop,
        "spp_psi": spp,
        "flow_in_gpm": flow_in,
        "flow_out_gpm": flow_out,
        "pit_vol_bbl": pit,
        "mw_ppg": mw,
        "gas_pct": gas,
    })
    return df
