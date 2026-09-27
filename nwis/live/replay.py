"""Live replay of a well's drilling log.

ReplaySession streams LiveSample rows in wall-clock time scaled by `speed`, keeping a
rolling window and feeding it through nwis.live.alerts.AlertEngine as new samples
arrive. Runs as an asyncio task so it slots straight into a FastAPI background task.

If nwis.synth hasn't written data/synthetic/logs/<well_id>.parquet yet (or writes a
different schema), we generate a simple synthetic log on the fly -- a linear-ROP log
with a torque ramp (stuck-pipe precursor) and a pit-loss episode (mud-loss precursor)
-- so the live demo never breaks standalone.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

from nwis.config import settings
from nwis.schema import LiveSample
from nwis.live.alerts import AlertEngine, PRECEDENT_CHECK_INTERVAL_M

WINDOW_SIZE = 120
LOG_COLUMNS = list(LiveSample.model_fields.keys())


# --------------------------------------------------------------------------- #
# Fallback synthetic log
# --------------------------------------------------------------------------- #
def generate_synthetic_log(
    well_id: str, seed: int = 0, md_start: float = 2000.0, md_end: float = 2700.0, step_m: float = 1.0,
) -> tuple[pd.DataFrame, dict]:
    """Linear ROP + a torque ramp (stuck-pipe precursor) + a pit-loss episode
    (mud-loss precursor), 1 m/step. Returns (df, truth) where `truth` maps
    hazard -> depth_md_m of the event this log was built around, for honest
    lead-distance reporting when data/synthetic/events_truth.json doesn't exist.
    """
    rng = np.random.default_rng(seed)
    mds = np.arange(md_start, md_end + step_m, step_m)
    n = len(mds)

    base_rop = 15.0
    rop = base_rop + rng.normal(0, 0.6, n)

    stuck_depth = md_start + 0.65 * (md_end - md_start)
    ramp_len_m = 80.0
    ramp_start = stuck_depth - ramp_len_m
    torque = 8.0 + rng.normal(0, 0.25, n)
    for i, md in enumerate(mds):
        if ramp_start <= md <= stuck_depth:
            frac = (md - ramp_start) / ramp_len_m
            torque[i] += 10.0 * frac ** 1.5
            rop[i] -= 8.0 * frac ** 1.5
    rop = np.clip(rop, 1.0, None)

    pit_loss_start = md_start + 0.25 * (md_end - md_start)
    pit_loss_len_m = 40.0
    pit_vol = 500.0 + rng.normal(0, 1.0, n)
    flow_in = 350.0 + rng.normal(0, 3.0, n)
    flow_out = flow_in + rng.normal(0, 2.0, n)
    for i, md in enumerate(mds):
        if pit_loss_start <= md <= pit_loss_start + pit_loss_len_m:
            frac = (md - pit_loss_start) / pit_loss_len_m
            pit_vol[i] -= 60.0 * frac
            flow_out[i] -= 25.0 * frac

    wob = 18.0 + rng.normal(0, 1.0, n)
    rpm_ = 120.0 + rng.normal(0, 4.0, n)
    spp = 2400.0 + rng.normal(0, 20.0, n)
    mw = 10.5 + rng.normal(0, 0.02, n)
    gas = np.clip(20.0 + rng.normal(0, 2.0, n), 0, None)

    rop_safe = np.clip(rop, 1.0, None)
    dt_hr = step_m / rop_safe
    t_s = np.concatenate([[0.0], np.cumsum(dt_hr[:-1] * 3600.0)])

    df = pd.DataFrame({
        "well_id": well_id, "t_s": t_s, "depth_md_m": mds,
        "wob_klbf": wob, "rpm": rpm_, "torque_kftlb": torque, "rop_m_hr": rop,
        "spp_psi": spp, "flow_in_gpm": flow_in, "flow_out_gpm": flow_out,
        "pit_vol_bbl": pit_vol, "mw_ppg": mw, "gas_pct": gas,
    })
    # "truth" depth = where the hazard would actually be logged as an event (end of
    # the precursor episode), not its first onset -- a real driller doesn't call a
    # "mud loss event" on the very first barrel; the precursor must be sustained
    # for the anomaly detector's lead-distance number to be honest.
    truth = {"stuck_pipe": float(stuck_depth), "mud_loss": float(pit_loss_start + pit_loss_len_m)}
    return df, truth


def load_log(well_id: str) -> tuple[pd.DataFrame, dict]:
    """Load data/synthetic/logs/<well_id>.parquet if nwis.synth has written it and it
    matches the LiveSample schema; otherwise fall back to the on-the-fly generator."""
    path = settings.synthetic_dir / "logs" / f"{well_id}.parquet"
    if path.exists():
        try:
            df = pd.read_parquet(path)
            if all(c in df.columns for c in LOG_COLUMNS):
                return df.sort_values("t_s").reset_index(drop=True), {}
        except Exception:
            pass  # corrupt/partial file from a concurrent writer -> demo must not break
    return generate_synthetic_log(well_id)


def _lookup_formation(well_id: str, md: float) -> Optional[str]:
    """Best-effort formation-at-depth via nwis.geo, if it has landed. Contract per
    IMPLEMENTATION_PLAN: formation_offset(well_id, md) -> (formation, offset_m)."""
    try:
        from nwis.geo.correlate import formation_offset
    except ImportError:
        return None
    try:
        result = formation_offset(well_id, md)
    except Exception:
        return None
    if isinstance(result, tuple):
        return result[0] if result else None
    if isinstance(result, dict):
        return result.get("formation")
    return getattr(result, "formation", None) if result is not None else None


# --------------------------------------------------------------------------- #
class ReplaySession:
    """Streams `well_id`'s log in wall-clock time scaled by `speed`."""

    def __init__(
        self, well_id: str, speed: float = 20.0,
        source: Optional[Union[str, Path, pd.DataFrame]] = None,
        alert_engine: Optional[AlertEngine] = None,
    ):
        self.well_id = well_id
        self.speed = speed
        self.truth: dict = {}

        if isinstance(source, pd.DataFrame):
            self.df = source.sort_values("t_s").reset_index(drop=True)
        elif source is not None:
            self.df = pd.read_parquet(source).sort_values("t_s").reset_index(drop=True)
        else:
            self.df, self.truth = load_log(well_id)

        self.alert_engine = alert_engine or AlertEngine()
        self.window: deque = deque(maxlen=WINDOW_SIZE)
        self.alerts_history: list = []
        self._idx = 0
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._paused = False
        self._sim_t0 = 0.0
        self._wall_t0 = 0.0
        self._last_sample: Optional[dict] = None
        self._formation: Optional[str] = None
        self._last_lookahead_md: Optional[float] = None

    # ------------------------------------------------------------------ #
    # control
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._task is not None:
            return
        self._running = True
        self._paused = False
        self._sim_t0 = float(self.df["t_s"].iloc[self._idx]) if len(self.df) else 0.0
        self._wall_t0 = time.monotonic()
        self._task = asyncio.create_task(self._run())

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if not self._paused:
            return
        if self._last_sample is not None:
            self._sim_t0 = float(self._last_sample["t_s"])
        self._wall_t0 = time.monotonic()
        self._paused = False

    def seek(self, md: float) -> None:
        if self.df.empty:
            return
        idx = int((self.df["depth_md_m"] - md).abs().idxmin())
        self._idx = idx
        self.window.clear()
        start = max(0, idx - WINDOW_SIZE + 1)
        for _, row in self.df.iloc[start:idx].iterrows():
            self.window.append(row.to_dict())
        if idx < len(self.df):
            self._last_sample = self.df.iloc[idx].to_dict()
            self._sim_t0 = float(self.df["t_s"].iloc[idx])
        self._wall_t0 = time.monotonic()

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------ #
    async def _run(self) -> None:
        try:
            while self._running and self._idx < len(self.df):
                if self._paused:
                    await asyncio.sleep(0.05)
                    continue
                target_sim_t = self._sim_t0 + (time.monotonic() - self._wall_t0) * self.speed
                advanced = False
                while self._idx < len(self.df) and float(self.df["t_s"].iloc[self._idx]) <= target_sim_t:
                    self._consume(self.df.iloc[self._idx])
                    self._idx += 1
                    advanced = True
                if not advanced:
                    await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            pass

    def _consume(self, row: pd.Series) -> None:
        sample = row.to_dict()
        self._last_sample = sample
        self.window.append(sample)
        window_df = pd.DataFrame(self.window)
        formation = _lookup_formation(self.well_id, sample["depth_md_m"])
        if formation is not None:
            self._formation = formation

        new_alerts = []
        new_alerts += self.alert_engine.process_param_anomaly(
            well_id=self.well_id, t_s=sample["t_s"], depth_md_m=sample["depth_md_m"],
            formation=self._formation, window_df=window_df,
        )

        # precedent_zone / model_risk hit the DB or a model per call -- only re-check
        # every PRECEDENT_CHECK_INTERVAL_M of new depth, not on every single sample.
        md = sample["depth_md_m"]
        if self._last_lookahead_md is None or abs(md - self._last_lookahead_md) >= PRECEDENT_CHECK_INTERVAL_M:
            self._last_lookahead_md = md
            new_alerts += self.alert_engine.process_precedent_zone(
                well_id=self.well_id, t_s=sample["t_s"], current_md=md, formation=self._formation,
            )
            new_alerts += self.alert_engine.process_model_risk(
                well_id=self.well_id, t_s=sample["t_s"], depth_md_m=md,
            )
        self.alerts_history.extend(new_alerts)

    # ------------------------------------------------------------------ #
    def state(self) -> dict:
        window_df = pd.DataFrame(self.window) if self.window else pd.DataFrame(columns=LOG_COLUMNS)
        stats: dict = {}
        if not window_df.empty:
            for col in ("torque_kftlb", "spp_psi", "pit_vol_bbl", "gas_pct", "rop_m_hr"):
                if col in window_df.columns:
                    stats[col] = {
                        "mean": float(window_df[col].mean()),
                        "std": float(window_df[col].std(ddof=0)),
                        "last": float(window_df[col].iloc[-1]),
                    }
        return {
            "well_id": self.well_id,
            "t_s": self._last_sample["t_s"] if self._last_sample else None,
            "depth_md_m": self._last_sample["depth_md_m"] if self._last_sample else None,
            "formation": self._formation,
            "last_sample": self._last_sample,
            "window_stats": stats,
            "alerts_open": [a.model_dump() for a in self.alert_engine.open_alerts(include_acked=True)],
            "alerts_history": [a.model_dump() for a in self.alert_engine.history],
            "signals_unavailable": sorted(self.alert_engine.signals_unavailable),
        }
