"""Minimum-curvature directional survey math + simple S-shaped well-path planner.

Own implementation (no wellpathpy dependency, per docs/IMPLEMENTATION_PLAN.md ground rules).
Pure numpy. All angles in degrees on the public API; radians internally.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TrajectoryTable:
    """Dense MD -> (TVD, N, E) table for one well, plus a monotonic MD<->TVD interpolator."""
    md: np.ndarray
    inc_deg: np.ndarray
    azi_deg: np.ndarray
    tvd: np.ndarray
    north: np.ndarray
    east: np.ndarray

    def tvd_at_md(self, md_query: float | np.ndarray) -> float | np.ndarray:
        return np.interp(md_query, self.md, self.tvd)

    def md_at_tvd(self, tvd_query: float | np.ndarray) -> float | np.ndarray:
        # self.tvd is monotonic increasing (inc < 90 deg always in this generator)
        return np.interp(tvd_query, self.tvd, self.md)

    def ne_at_md(self, md_query: float) -> tuple[float, float]:
        n = float(np.interp(md_query, self.md, self.north))
        e = float(np.interp(md_query, self.md, self.east))
        return n, e


def minimum_curvature(md: np.ndarray, inc_deg: np.ndarray, azi_deg: np.ndarray) -> TrajectoryTable:
    """Classic minimum-curvature method. Station 0 is assumed to be surface (MD=0)."""
    md = np.asarray(md, dtype=float)
    inc = np.radians(np.asarray(inc_deg, dtype=float))
    azi = np.radians(np.asarray(azi_deg, dtype=float))

    n = len(md)
    tvd = np.zeros(n)
    north = np.zeros(n)
    east = np.zeros(n)

    for i in range(1, n):
        d_md = md[i] - md[i - 1]
        i1, i2 = inc[i - 1], inc[i]
        a1, a2 = azi[i - 1], azi[i]

        cos_dl = np.cos(i2 - i1) - np.sin(i1) * np.sin(i2) * (1 - np.cos(a2 - a1))
        cos_dl = np.clip(cos_dl, -1.0, 1.0)
        dl = np.arccos(cos_dl)

        if dl < 1e-9:
            rf = 1.0
        else:
            rf = (2.0 / dl) * np.tan(dl / 2.0)

        d_tvd = (d_md / 2.0) * (np.cos(i1) + np.cos(i2)) * rf
        d_n = (d_md / 2.0) * (np.sin(i1) * np.cos(a1) + np.sin(i2) * np.cos(a2)) * rf
        d_e = (d_md / 2.0) * (np.sin(i1) * np.sin(a1) + np.sin(i2) * np.sin(a2)) * rf

        tvd[i] = tvd[i - 1] + d_tvd
        north[i] = north[i - 1] + d_n
        east[i] = east[i - 1] + d_e

    return TrajectoryTable(md=md, inc_deg=np.degrees(inc), azi_deg=np.degrees(azi),
                            tvd=tvd, north=north, east=east)


def build_station_plan(md_max_guess: float, trajectory_type: str, rng: np.random.Generator,
                        step_m: float = 30.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build an MD/inc/azi station plan for a vertical or deviated (build-hold-drop, "S"-shaped) well.

    `md_max_guess` should be generous (beyond expected TD) so the caller can later truncate the
    resulting table at the exact TVD that matches the target total depth.
    """
    md = np.arange(0.0, md_max_guess + step_m, step_m)

    if trajectory_type == "vertical":
        inc = np.zeros_like(md)
        azi = np.zeros_like(md)
        return md, inc, azi

    kickoff = rng.uniform(400.0, 900.0)
    target_inc = rng.uniform(15.0, 35.0)
    build_rate = rng.uniform(1.5, 3.0)  # deg per 30 m
    build_len = max(step_m, (target_inc / build_rate) * step_m)
    hold_len = rng.uniform(300.0, 900.0)
    drop_rate = rng.uniform(1.0, 2.5)
    drop_len = max(step_m, (target_inc / drop_rate) * step_m)
    azi_const = rng.uniform(0.0, 360.0)
    final_inc = rng.uniform(0.0, 4.0)  # S-shape: drop back near-vertical, not necessarily to 0

    build_start = kickoff
    build_end = build_start + build_len
    hold_end = build_end + hold_len
    drop_end = hold_end + drop_len

    inc = np.empty_like(md)
    for idx, m in enumerate(md):
        if m <= build_start:
            inc[idx] = 0.0
        elif m <= build_end:
            inc[idx] = np.interp(m, [build_start, build_end], [0.0, target_inc])
        elif m <= hold_end:
            inc[idx] = target_inc
        elif m <= drop_end:
            inc[idx] = np.interp(m, [hold_end, drop_end], [target_inc, final_inc])
        else:
            inc[idx] = final_inc

    azi = np.where(inc > 1e-6, azi_const, 0.0)
    return md, inc, azi


def trajectory_for_target_tvd(target_tvd: float, trajectory_type: str, rng: np.random.Generator,
                               step_m: float = 30.0) -> TrajectoryTable:
    """Build a station plan long enough to reach `target_tvd`, then truncate exactly there."""
    guess = target_tvd * (1.25 if trajectory_type == "deviated" else 1.02) + 200.0
    md, inc, azi = build_station_plan(guess, trajectory_type, rng, step_m)
    table = minimum_curvature(md, inc, azi)

    if table.tvd[-1] < target_tvd:
        # Extremely rare with the guess factor above; extend with a vertical tail.
        extra_md = np.arange(md[-1] + step_m, md[-1] + step_m + target_tvd, step_m)
        extra_inc = np.full_like(extra_md, inc[-1])
        extra_azi = np.full_like(extra_md, azi[-1])
        md = np.concatenate([md, extra_md])
        inc = np.concatenate([inc, extra_inc])
        azi = np.concatenate([azi, extra_azi])
        table = minimum_curvature(md, inc, azi)

    md_at_target = float(table.md_at_tvd(target_tvd))
    keep = table.md <= md_at_target + 1e-6
    md_k = np.append(table.md[keep], md_at_target) if table.md[keep][-1] < md_at_target - 1e-6 else table.md[keep]
    inc_k = np.interp(md_k, table.md, table.inc_deg)
    azi_k = np.interp(md_k, table.md, table.azi_deg)
    final = minimum_curvature(md_k, inc_k, azi_k)
    # Force the exact target (interpolation/round-trip may be off by sub-metre noise).
    final.tvd[-1] = target_tvd
    return final
