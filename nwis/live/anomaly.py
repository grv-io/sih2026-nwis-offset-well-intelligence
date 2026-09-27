"""Rolling anomaly detection over the live-sample window.

Pure functions of a pandas DataFrame window (columns = nwis.schema.LiveSample fields).
No I/O, no imports of other live-pipeline modules here -> safe to unit test in isolation.

Design choice: the "recent" side of every z-score is the LAST sample only (not an
average of the last few). This is what makes the N-of-M gate in alerts.py behave
correctly: a true single-sample sensor blip produces exactly one flagged call, while
a real multi-sample ramp/episode produces a flagged call on every sample it persists
through. Averaging the tail would smear a single blip across several subsequent
calls and defeat the N-of-M "reject a single spike" guarantee.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nwis.schema import EventType as ET

# rule name -> hazard it maps to (per IMPLEMENTATION_PLAN Phase 6)
HAZARD_MAP: dict[str, ET] = {
    "pit_loss": ET.mud_loss,
    "pit_gain": ET.kick,
    "torque_ramp": ET.stuck_pipe,
    "spp_spike": ET.overpressure,
    "gas_rise": ET.kick,
}

MIN_WINDOW = 30  # samples needed before z-scores are meaningful (8 fired a spurious alert at MD 12 m on start-up, 26 Sep)
BASELINE_FRAC = 0.7


def _zscore(series: np.ndarray) -> float:
    """z-score of the LAST value against the mean/std of the earlier BASELINE_FRAC
    of the series. Returns 0.0 if the window is too short or the baseline is flat."""
    n = len(series)
    if n < MIN_WINDOW:
        return 0.0
    split = max(5, int(n * BASELINE_FRAC))
    baseline = series[:split]
    value = float(series[-1])
    mu = float(baseline.mean())
    sd = float(baseline.std())
    if sd < 1e-9:
        sd = 1e-9
    return (value - mu) / sd


def zscores(window_df: pd.DataFrame) -> dict[str, float]:
    """Rolling z-scores per parameter: torque, spp, pit_vol_bbl, flow_out-flow_in, gas_pct, rop."""
    if window_df is None or window_df.empty:
        return {}
    out: dict[str, float] = {}
    try:
        out["torque_kftlb"] = _zscore(window_df["torque_kftlb"].to_numpy(dtype=float))
        out["spp_psi"] = _zscore(window_df["spp_psi"].to_numpy(dtype=float))
        out["pit_vol_bbl"] = _zscore(window_df["pit_vol_bbl"].to_numpy(dtype=float))
        flow_diff = (window_df["flow_out_gpm"] - window_df["flow_in_gpm"]).to_numpy(dtype=float)
        out["flow_out_minus_in_gpm"] = _zscore(flow_diff)
        out["gas_pct"] = _zscore(window_df["gas_pct"].to_numpy(dtype=float))
        out["rop_m_hr"] = _zscore(window_df["rop_m_hr"].to_numpy(dtype=float))
    except (KeyError, ValueError, TypeError):
        pass
    return out


Flag = tuple[bool, float, dict]


def pit_loss(window_df: pd.DataFrame) -> Flag:
    """Pit volume falling & flow_out < flow_in -> mud loss precursor."""
    z = _zscore(window_df["pit_vol_bbl"].to_numpy(dtype=float))
    flow_in = float(window_df["flow_in_gpm"].iloc[-1])
    flow_out = float(window_df["flow_out_gpm"].iloc[-1])
    flag = bool(z < -1.0 and flow_out < flow_in)
    strength = float(np.clip((-z) / 3.0, 0.0, 1.0)) if flag else 0.0
    return flag, strength, {"pit_vol_zscore": z, "flow_in_gpm": flow_in, "flow_out_gpm": flow_out}


def pit_gain(window_df: pd.DataFrame) -> Flag:
    """Pit volume rising & flow_out > flow_in -> kick precursor."""
    z = _zscore(window_df["pit_vol_bbl"].to_numpy(dtype=float))
    flow_in = float(window_df["flow_in_gpm"].iloc[-1])
    flow_out = float(window_df["flow_out_gpm"].iloc[-1])
    flag = bool(z > 1.0 and flow_out > flow_in)
    strength = float(np.clip(z / 3.0, 0.0, 1.0)) if flag else 0.0
    return flag, strength, {"pit_vol_zscore": z, "flow_in_gpm": flow_in, "flow_out_gpm": flow_out}


def torque_ramp(window_df: pd.DataFrame) -> Flag:
    """Torque rising while ROP falls -> stuck-pipe precursor."""
    tz = _zscore(window_df["torque_kftlb"].to_numpy(dtype=float))
    rz = _zscore(window_df["rop_m_hr"].to_numpy(dtype=float))
    flag = bool(tz > 2.0 and rz < -1.0)
    strength = float(np.clip(min(tz / 4.0, (-rz) / 3.0), 0.0, 1.0)) if flag else 0.0
    return flag, strength, {"torque_zscore": tz, "rop_zscore": rz}


def spp_spike(window_df: pd.DataFrame) -> Flag:
    """Standpipe pressure spike -> overpressure precursor."""
    z = _zscore(window_df["spp_psi"].to_numpy(dtype=float))
    flag = bool(z > 2.0)
    strength = float(np.clip(z / 4.0, 0.0, 1.0)) if flag else 0.0
    return flag, strength, {"spp_zscore": z}


def gas_rise(window_df: pd.DataFrame) -> Flag:
    """Gas percentage rising -> kick precursor."""
    z = _zscore(window_df["gas_pct"].to_numpy(dtype=float))
    flag = bool(z > 2.0)
    strength = float(np.clip(z / 4.0, 0.0, 1.0)) if flag else 0.0
    return flag, strength, {"gas_zscore": z}


RULES = {
    "pit_loss": pit_loss,
    "pit_gain": pit_gain,
    "torque_ramp": torque_ramp,
    "spp_spike": spp_spike,
    "gas_rise": gas_rise,
}


def evaluate_flags(window_df: pd.DataFrame) -> dict[str, Flag]:
    """Run every domain rule against the current window. Never raises -- a rule that
    can't compute (missing column, too-short window) degrades to (False, 0.0, {})."""
    out: dict[str, Flag] = {}
    if window_df is None or window_df.empty or len(window_df) < MIN_WINDOW:
        return {name: (False, 0.0, {}) for name in RULES}
    for name, fn in RULES.items():
        try:
            out[name] = fn(window_df)
        except (KeyError, ValueError, TypeError, IndexError):
            out[name] = (False, 0.0, {})
    return out


def debug_signals(window_df: pd.DataFrame) -> dict:
    """Everything /live/signals needs: raw z-scores + rule flags/strength/evidence."""
    flags = evaluate_flags(window_df)
    return {
        "zscores": zscores(window_df),
        "flags": {
            name: {"flag": flag, "strength": strength, "evidence": evidence, "hazard": HAZARD_MAP[name].value}
            for name, (flag, strength, evidence) in flags.items()
        },
    }
