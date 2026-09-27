"""Phase 6 tests: no LLM, no other module's data required.

nwis.geo / nwis.search / nwis.predict have landed in this repo, but these tests
still exercise the standalone-degradation path explicitly (test_precedent_zone_*)
by monkeypatching sys.modules, so the alert engine's lazy-import contract stays
covered regardless of what those modules ship next.
"""
from __future__ import annotations

import sys
import types
from collections import deque

import numpy as np
import pandas as pd
import pytest

from nwis.schema import EventType as ET, Severity
from nwis.live.alerts import AlertEngine
from nwis.live.replay import WINDOW_SIZE, generate_synthetic_log


def feed(engine: AlertEngine, df: pd.DataFrame, formation=None) -> list:
    """Replay a DataFrame through the engine sample-by-sample, like ReplaySession does."""
    window: deque = deque(maxlen=WINDOW_SIZE)
    alerts = []
    for _, row in df.iterrows():
        sample = row.to_dict()
        window.append(sample)
        window_df = pd.DataFrame(window)
        alerts += engine.process_param_anomaly(
            well_id=sample["well_id"], t_s=sample["t_s"], depth_md_m=sample["depth_md_m"],
            formation=formation, window_df=window_df,
        )
    return alerts


def _flat_df(well_id: str, n: int, dt_s: float = 30.0, seed: int = 0, **overrides) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = {
        "well_id": well_id,
        "t_s": np.arange(n) * dt_s,
        "depth_md_m": 2000.0 + np.arange(n) * 0.5,
        "wob_klbf": 18.0 + rng.normal(0, 0.2, n),
        "rpm": 120.0 + rng.normal(0, 1.0, n),
        "torque_kftlb": 8.0 + rng.normal(0, 0.1, n),
        "rop_m_hr": 15.0 + rng.normal(0, 0.2, n),
        "spp_psi": 2400.0 + rng.normal(0, 5.0, n),
        "flow_in_gpm": 350.0 + rng.normal(0, 1.0, n),
        "flow_out_gpm": 350.0 + rng.normal(0, 1.0, n),
        "pit_vol_bbl": 500.0 + rng.normal(0, 0.5, n),
        "mw_ppg": 10.5 + np.zeros(n),
        "gas_pct": 20.0 + rng.normal(0, 0.5, n),
    }
    df = pd.DataFrame(data)
    for col, values in overrides.items():
        df[col] = values
    return df


# --------------------------------------------------------------------------- #
# param_anomaly: torque ramp -> stuck_pipe, with a positive lead distance
# --------------------------------------------------------------------------- #
def test_torque_ramp_triggers_one_stuck_pipe_alert_with_positive_lead():
    df, truth = generate_synthetic_log("TEST-STUCK", seed=1)
    engine = AlertEngine()
    alerts = feed(engine, df)

    stuck = [a for a in alerts if a.hazard == ET.stuck_pipe and a.rule == "param_anomaly"]
    assert len(stuck) == 1, f"expected exactly one stuck_pipe alert, got {[a.hazard for a in alerts]}"

    lead_m = truth["stuck_pipe"] - stuck[0].depth_md_m
    assert lead_m > 0, "alert must fire BEFORE the bit reaches the stuck-pipe depth"
    assert stuck[0].recommendation is not None
    assert stuck[0].citations, "alert must always carry a citation"


# --------------------------------------------------------------------------- #
# param_anomaly: pit-loss episode -> exactly one mud_loss alert
# --------------------------------------------------------------------------- #
def test_pit_loss_triggers_one_mud_loss_alert():
    df, truth = generate_synthetic_log("TEST-PITLOSS", seed=2)
    engine = AlertEngine()
    alerts = feed(engine, df)

    mud = [a for a in alerts if a.hazard == ET.mud_loss and a.rule == "param_anomaly"]
    assert len(mud) == 1, f"expected exactly one mud_loss alert, got {[a.hazard for a in alerts]}"
    lead_m = truth["mud_loss"] - mud[0].depth_md_m
    assert lead_m > 0


# --------------------------------------------------------------------------- #
# N-of-M rejects an isolated single-sample spike
# --------------------------------------------------------------------------- #
def test_single_sample_spike_is_rejected_by_n_of_m():
    engine = AlertEngine()
    df = _flat_df("TEST-SPIKE", n=30, dt_s=300.0, seed=3)
    # isolated one-sample torque spike with no surrounding ramp
    df.loc[27, "torque_kftlb"] = 40.0
    df.loc[27, "rop_m_hr"] = 2.0

    alerts = feed(engine, df)
    assert alerts == [], f"a single-sample spike must not clear the N-of-M gate, got {alerts}"


# --------------------------------------------------------------------------- #
# cooldown prevents a duplicate alert for the same (well, hazard, rule), and
# lets a genuinely new one through once it expires
# --------------------------------------------------------------------------- #
def _rows(well_id, t_values, pit_vol, flow_out, depth0=2000.0):
    n = len(t_values)
    return pd.DataFrame({
        "well_id": well_id, "t_s": t_values, "depth_md_m": depth0 + np.arange(n) * 0.5,
        "wob_klbf": 18.0, "rpm": 120.0, "torque_kftlb": 8.0, "rop_m_hr": 15.0,
        "spp_psi": 2400.0, "flow_in_gpm": 350.0, "flow_out_gpm": flow_out,
        "pit_vol_bbl": pit_vol, "mw_ppg": 10.5, "gas_pct": 20.0,
    })


def _feed_rows(engine, window, df):
    alerts = []
    for _, row in df.iterrows():
        sample = row.to_dict()
        window.append(sample)
        alerts += engine.process_param_anomaly(
            well_id=sample["well_id"], t_s=sample["t_s"], depth_md_m=sample["depth_md_m"],
            formation=None, window_df=pd.DataFrame(window),
        )
    return alerts


def test_cooldown_prevents_duplicate_then_allows_refire_after_expiry():
    engine = AlertEngine()  # default cooldown 120s, dwell 20s, N-of-M (3, 5)
    well = "TEST-COOLDOWN"
    dt = 30.0

    baseline_t = list(np.arange(34) * dt)                          # 34 flat samples (>= anomaly.MIN_WINDOW)
    episode_t = [baseline_t[-1] + dt * k for k in range(1, 6)]      # 300..420, sharp pit-vol drop
    recovery_t = [episode_t[-1] + dt]                               # 450, back to baseline

    df1 = pd.concat([
        _rows(well, baseline_t, [500.0] * 34, [350.0] * 34),
        _rows(well, episode_t, [460.0] * 5, [330.0] * 5),
        _rows(well, recovery_t, [500.0], [350.0]),
    ], ignore_index=True)

    window: deque = deque(maxlen=WINDOW_SIZE)
    mud1 = [a for a in _feed_rows(engine, window, df1) if a.hazard == ET.mud_loss]
    assert len(mud1) == 1
    fire_t = mud1[0].t_s
    assert engine.shelve(mud1[0].alert_id)  # clear dedupe so only cooldown can block a re-fire

    # same sustained anomaly, well inside the 120s cooldown -> must NOT re-alert
    df2 = _rows(well, [fire_t + 10, fire_t + 60], [460.0, 460.0], [330.0, 330.0])
    mud2 = [a for a in _feed_rows(engine, window, df2) if a.hazard == ET.mud_loss]
    assert mud2 == [], "cooldown should suppress a re-fire immediately after the first alert"

    # once the cooldown has expired, the same anomaly is allowed to alert again
    df3 = _rows(well, [fire_t + 130, fire_t + 160], [460.0, 460.0], [330.0, 330.0])
    mud3 = [a for a in _feed_rows(engine, window, df3) if a.hazard == ET.mud_loss]
    assert len(mud3) == 1, "cooldown should have expired by now, allowing a fresh alert"


# --------------------------------------------------------------------------- #
# precedent_zone: lookahead advisory fires ahead of the bit, not behind it
# --------------------------------------------------------------------------- #
def _install_fake_lookahead(monkeypatch, events):
    fake_mod = types.ModuleType("nwis.geo.correlate")
    fake_mod.active_well_lookahead = lambda *a, **k: events
    monkeypatch.setitem(sys.modules, "nwis.geo.correlate", fake_mod)


def test_precedent_zone_fires_for_events_ahead_of_the_bit(monkeypatch):
    current_md = 2400.0
    events = [
        {"event_id": "E1", "source_well_id": "DUL-003", "event_type": "kick",
         "expected_md_m": current_md + 30, "formation": "Barail"},
        {"event_id": "E2", "source_well_id": "DUL-007", "event_type": "kick",
         "expected_md_m": current_md + 60, "formation": "Barail"},
        {"event_id": "E3", "source_well_id": "MOR-002", "event_type": "kick",
         "expected_md_m": current_md + 100, "formation": "Barail"},
    ]
    _install_fake_lookahead(monkeypatch, events)

    engine = AlertEngine()
    alerts = engine.process_precedent_zone(
        well_id="ACTIVE-01", t_s=0.0, current_md=current_md, formation="Barail",
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.hazard == ET.kick
    assert alert.rule == "precedent_zone"
    assert set(alert.precedent_event_ids) == {"E1", "E2", "E3"}
    assert alert.citations


def test_precedent_zone_ignores_events_behind_the_bit(monkeypatch):
    current_md = 2400.0
    events = [
        {"event_id": "E1", "source_well_id": "DUL-003", "event_type": "kick",
         "expected_md_m": current_md - 30, "formation": "Barail"},
        {"event_id": "E2", "source_well_id": "DUL-007", "event_type": "kick",
         "expected_md_m": current_md - 60, "formation": "Barail"},
        {"event_id": "E3", "source_well_id": "MOR-002", "event_type": "kick",
         "expected_md_m": current_md - 10, "formation": "Barail"},
    ]
    _install_fake_lookahead(monkeypatch, events)

    engine = AlertEngine()
    alerts = engine.process_precedent_zone(
        well_id="ACTIVE-01", t_s=0.0, current_md=current_md, formation="Barail",
    )
    assert alerts == []


# --------------------------------------------------------------------------- #
# ack
# --------------------------------------------------------------------------- #
def test_ack_marks_alert_acknowledged():
    df, _truth = generate_synthetic_log("TEST-ACK", seed=1)
    engine = AlertEngine()
    alerts = feed(engine, df)
    assert alerts, "fixture must produce at least one alert to ack"
    alert_id = alerts[0].alert_id

    assert engine.ack(alert_id) is True
    assert engine.open[alert_id].acknowledged is True
    assert alert_id not in {a.alert_id for a in engine.open_alerts(include_acked=False)}
    assert alert_id in {a.alert_id for a in engine.open_alerts(include_acked=True)}
    assert engine.ack("no-such-id") is False


# --------------------------------------------------------------------------- #
# open-alert cap evicts the lowest-severity alert
# --------------------------------------------------------------------------- #
def test_open_alert_cap_evicts_lowest_severity():
    engine = AlertEngine(max_open_alerts=2)
    from nwis.schema import Alert

    low = Alert(alert_id="A-LOW", well_id="W1", t_s=0.0, depth_md_m=2000.0,
                rule="param_anomaly", hazard=ET.overpressure, severity=Severity.low, message="low")
    med = Alert(alert_id="A-MED", well_id="W1", t_s=10.0, depth_md_m=2010.0,
                rule="param_anomaly", hazard=ET.stuck_pipe, severity=Severity.medium, message="med")
    high = Alert(alert_id="A-HIGH", well_id="W1", t_s=20.0, depth_md_m=2020.0,
                 rule="param_anomaly", hazard=ET.kick, severity=Severity.high, message="high")

    engine._register(low, ("W1", "overpressure", "param_anomaly"))
    engine._register(med, ("W1", "stuck_pipe", "param_anomaly"))
    assert len(engine.open) == 2

    engine._register(high, ("W1", "kick", "param_anomaly"))
    assert len(engine.open) == 2
    assert "A-LOW" not in engine.open, "lowest-severity open alert should have been evicted"
    assert "A-MED" in engine.open
    assert "A-HIGH" in engine.open
    # eviction never touches history -- it's still the audit trail
    assert {a.alert_id for a in engine.history} == {"A-LOW", "A-MED", "A-HIGH"}
