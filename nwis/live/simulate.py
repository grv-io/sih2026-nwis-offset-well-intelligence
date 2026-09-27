"""Headless replay + alert demo, no API, no LLM.

    python -m nwis.live.simulate --well DUL-005 --speed 200 --headless

Prints a timeline of alerts as they fire and an honest exit summary: n_alerts by
rule/hazard, and the first alert's lead distance in metres before the truth event's
depth (from data/synthetic/events_truth.json if nwis.synth has written one for this
well, else from the on-the-fly generator's own ground truth).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, deque

import pandas as pd

from nwis.config import settings
from nwis.live.alerts import AlertEngine, PRECEDENT_CHECK_INTERVAL_M
from nwis.live.replay import WINDOW_SIZE, load_log


def _load_truth_depths(well_id: str, fallback: dict) -> dict:
    """event_type -> depth_md_m, preferring the shared events_truth.json (real synth
    data) and falling back to the on-the-fly generator's own truth otherwise."""
    truth_path = settings.synthetic_dir / "events_truth.json"
    if truth_path.exists():
        try:
            data = json.loads(truth_path.read_text())
            records = data.get(well_id, data) if isinstance(data, dict) else data
            out: dict = {}
            for ev in records:
                if not isinstance(ev, dict) or ev.get("well_id", well_id) != well_id:
                    continue
                et = ev.get("event_type") or ev.get("hazard")
                md = ev.get("depth_md_m")
                if et and md is not None and et not in out:
                    out[et] = float(md)
            if out:
                return out
        except Exception:
            pass  # malformed/partial file from a concurrent writer -> honest fallback below
    return fallback


def run_headless(well_id: str, speed: float = 200.0) -> dict:
    df, truth = load_log(well_id)
    engine = AlertEngine()
    window: deque = deque(maxlen=WINDOW_SIZE)
    alerts = []
    last_lookahead_md = None

    for _, row in df.iterrows():
        sample = row.to_dict()
        window.append(sample)
        window_df = pd.DataFrame(window)

        new = []
        new += engine.process_param_anomaly(
            well_id=well_id, t_s=sample["t_s"], depth_md_m=sample["depth_md_m"],
            formation=None, window_df=window_df,
        )

        # precedent_zone / model_risk hit the DB or a model per call -- only re-check
        # every PRECEDENT_CHECK_INTERVAL_M of new depth, not on every single sample.
        md = sample["depth_md_m"]
        if last_lookahead_md is None or abs(md - last_lookahead_md) >= PRECEDENT_CHECK_INTERVAL_M:
            last_lookahead_md = md
            new += engine.process_precedent_zone(well_id=well_id, t_s=sample["t_s"], current_md=md)
            new += engine.process_model_risk(well_id=well_id, t_s=sample["t_s"], depth_md_m=md)
        for a in new:
            print(
                f"[t={a.t_s:8.0f}s md={a.depth_md_m:7.1f}m] {a.severity.value.upper():8s} "
                f"{a.rule:14s} {a.hazard.value:12s} {a.message}"
            )
        alerts.extend(new)

    truth_depths = _load_truth_depths(well_id, truth)

    by_rule = Counter(a.rule for a in alerts)
    by_hazard = Counter(a.hazard.value for a in alerts)

    first_alert_note = "no alerts fired"
    lead_distance_m = None
    if alerts:
        first = min(alerts, key=lambda a: a.t_s)
        first_alert_note = f"{first.rule}/{first.hazard.value} @ md={first.depth_md_m:.1f}m (t={first.t_s:.0f}s)"
        truth_md = truth_depths.get(first.hazard.value)
        if truth_md is not None:
            lead_distance_m = round(truth_md - first.depth_md_m, 1)

    return {
        "well_id": well_id,
        "speed": speed,
        "n_samples": int(len(df)),
        "n_alerts": len(alerts),
        "by_rule": dict(by_rule),
        "by_hazard": dict(by_hazard),
        "first_alert": first_alert_note,
        "first_alert_lead_distance_m": lead_distance_m,
        "signals_unavailable": sorted(engine.signals_unavailable),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Headless NWIS live replay + alert demo (no API, no LLM)")
    ap.add_argument("--well", required=True, help="well_id, e.g. DUL-005")
    ap.add_argument("--speed", type=float, default=200.0, help="accepted for interface parity; headless "
                                                                "mode runs samples through as fast as possible")
    ap.add_argument("--headless", action="store_true", help="required flag; this CLI only runs headless")
    args = ap.parse_args()

    if not args.headless:
        raise SystemExit("nwis.live.simulate only supports --headless; use api/live_router.py for live replay")

    summary = run_headless(args.well, args.speed)
    print("\n=== Summary ===")
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
