"""Tests for nwis/predict/ (Phase 5).

No db.reset() anywhere in this file (per the Phase 5 build brief — a background
ingestion job is actively writing to data/nwis.sqlite). Every test either exercises a
pure function, or reads the shared DB / already-trained models/*.json artifacts
read-only. `python -m nwis.predict.train` must have been run at least once (it has;
models/xgb_*.json, models/risk_summary.json, models/formation_log_defaults.json and
models/predict_metrics.json all exist on disk) for the model-backed tests to be
meaningful; they're written to degrade gracefully (skip) if an artifact is missing.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from nwis import db
from nwis.config import settings
from nwis.predict import features as F
from nwis.predict import risk_ahead as R

WELL = "DUL-001"  # a real well from the shared synthetic field, present since Phase 1


def _have_well(well_id: str) -> bool:
    return db.get_well(well_id) is not None


pytestmark = pytest.mark.skipif(not _have_well(WELL), reason=f"{WELL} not found in data/nwis.sqlite")


# --------------------------------------------------------------------------- #
# 1. features.py — columns + no-NaN labels
# --------------------------------------------------------------------------- #
def test_features_build_for_one_well_has_expected_columns_and_no_nan_labels():
    df = F.build_features_for_well(WELL)
    assert not df.empty

    expected_cols = {
        "well_id", "top_md_m", "base_md_m", "formation", "offset_from_top_m",
        "fraction_of_formation", "mw_actual_mean", "torque_mean", "torque_max", "torque_slope",
        "spp_mean", "gas_mean", "pit_delta", "flow_delta",
    }
    expected_cols |= {f"label_{h}" for h in F.PRIMARY_HAZARDS}
    expected_cols |= {f"precedent_count_{h}" for h in F.ALL_HAZARDS}
    expected_cols |= {f"precedent_hours_{h}" for h in F.ALL_HAZARDS}
    expected_cols |= {f"precedent_simw_{h}" for h in F.ALL_HAZARDS}
    missing = expected_cols - set(df.columns)
    assert not missing, f"missing expected feature columns: {missing}"

    label_cols = [f"label_{h}" for h in F.PRIMARY_HAZARDS]
    assert df[label_cols].isna().sum().sum() == 0, "label columns must never contain NaN"
    for c in label_cols:
        assert set(df[c].unique()) <= {0, 1}

    # intervals must tile surface -> TD with no gaps/overlaps
    assert (df["base_md_m"] - df["top_md_m"] == settings.interval_m).all()
    assert (df.sort_values("top_md_m")["top_md_m"].diff().dropna() == settings.interval_m).all()


# --------------------------------------------------------------------------- #
# 2. lookahead label logic — pure function, the worked example from the brief
# --------------------------------------------------------------------------- #
def test_lookahead_label_logic_worked_example():
    lookahead_m = 4 * 50.0  # lookahead_intervals=4, interval_m=50
    event_depth = 2420.0

    positive_tops = [2250.0, 2300.0, 2350.0, 2400.0]
    negative_tops = [2200.0, 2450.0, 2500.0]

    for top in positive_tops:
        assert F.is_lookahead_positive(top, lookahead_m, event_depth), f"expected positive at top={top}"
    for top in negative_tops:
        assert not F.is_lookahead_positive(top, lookahead_m, event_depth), f"expected negative at top={top}"


def test_lookahead_label_logic_matches_configured_settings():
    """Same worked example, but driven off nwis.config.settings so this test breaks
    loudly if interval_m/lookahead_intervals ever change."""
    lookahead_m = settings.lookahead_intervals * settings.interval_m
    event_depth = 2420.0
    top_of_event_interval = (event_depth // settings.interval_m) * settings.interval_m
    # the event's own interval, and the (lookahead_intervals - 1) intervals before it, are positive
    for k in range(settings.lookahead_intervals):
        top = top_of_event_interval - k * settings.interval_m
        assert F.is_lookahead_positive(top, lookahead_m, event_depth)
    # one interval further back, and one interval ahead of the event, are negative
    assert not F.is_lookahead_positive(top_of_event_interval - settings.lookahead_intervals * settings.interval_m, lookahead_m, event_depth)
    assert not F.is_lookahead_positive(top_of_event_interval + settings.interval_m, lookahead_m, event_depth)


# --------------------------------------------------------------------------- #
# 3. fusion weights + score range
# --------------------------------------------------------------------------- #
def test_fusion_weights_sum_to_one():
    total = settings.risk_w_precedent + settings.risk_w_anomaly + settings.risk_w_model
    assert total == pytest.approx(1.0)


def test_score_is_in_unit_interval():
    intervals = R.score(WELL, 800.0)
    assert intervals
    for iv in intervals:
        assert 0.0 <= iv.score <= 1.0
        assert 0.0 <= iv.precedent_component <= 1.0
        assert 0.0 <= iv.model_component <= 1.0
        assert 0.0 <= iv.anomaly_component <= 1.0


# --------------------------------------------------------------------------- #
# 4. A2 mode switch — monkeypatched labelled-event count
# --------------------------------------------------------------------------- #
def test_mode_switch_flips_to_indicator_below_threshold(monkeypatch):
    formation, offset = "Girujan", 100.0
    precedent_feats = {f"precedent_{k}_stuck_pipe": 0.0 for k in ("count", "hours", "simw")}

    monkeypatch.setattr(R, "_get_truth_count", lambda hazard: R.MIN_LABELLED_EVENTS_FOR_SUPERVISED - 1)
    prob, mode, X_row, entry = R._model_probability("stuck_pipe", formation, offset, precedent_feats)
    assert mode == "indicator"
    assert X_row is None and entry is None
    assert 0.0 <= prob <= 1.0


def test_mode_switch_uses_supervised_when_model_exists_and_above_threshold(monkeypatch):
    if not (settings.models_dir / "xgb_stuck_pipe.json").exists():
        pytest.skip("models/xgb_stuck_pipe.json not trained yet")
    formation, offset = "Girujan", 100.0
    precedent_feats = {f"precedent_{k}_{h}": 0.0 for k in ("count", "hours", "simw") for h in F.ALL_HAZARDS}

    monkeypatch.setattr(R, "_get_truth_count", lambda hazard: R.MIN_LABELLED_EVENTS_FOR_SUPERVISED + 100)
    prob, mode, X_row, entry = R._model_probability("stuck_pipe", formation, offset, precedent_feats)
    assert mode == "supervised"
    assert X_row is not None and entry is not None
    assert 0.0 <= prob <= 1.0


def test_score_top_reasons_carries_mode_as_first_entry(monkeypatch):
    monkeypatch.setattr(R, "_get_truth_count", lambda hazard: 0)
    intervals = R.score(WELL, 800.0)
    for iv in intervals:
        assert iv.top_reasons, "top_reasons must never be empty"
        assert iv.top_reasons[0] == "mode:indicator"


# --------------------------------------------------------------------------- #
# 5. score() shape + warm performance
# --------------------------------------------------------------------------- #
def test_score_returns_lookahead_intervals_sorted_by_depth_and_is_fast_warm():
    R.score(WELL, 900.0)  # warm-up: cold nearby_wells/model loads happen here

    t0 = time.time()
    intervals = R.score(WELL, 950.0)
    elapsed = time.time() - t0

    assert len(intervals) == settings.lookahead_intervals
    depths = [iv.top_md_m for iv in intervals]
    assert depths == sorted(depths)
    assert elapsed < 1.0, f"score() took {elapsed:.3f}s warm, expected < 1s"


# --------------------------------------------------------------------------- #
# 6. API smoke
# --------------------------------------------------------------------------- #
def test_api_risk_smoke():
    from api.main import app
    client = TestClient(app)

    r = client.get("/risk", params={"well_id": WELL, "md": 800.0})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == settings.lookahead_intervals
    assert {"well_id", "top_md_m", "base_md_m", "hazard", "score", "top_reasons"} <= set(body[0].keys())

    r = client.get("/risk", params={"well_id": "NO-SUCH-WELL", "md": 800.0})
    assert r.status_code == 404

    r = client.get("/risk/metrics")
    assert r.status_code == 200
    assert "hazards" in r.json()

    r = client.get("/risk/summary")
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
