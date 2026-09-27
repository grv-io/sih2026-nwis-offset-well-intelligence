"""Tests for nwis/geo/. No LLM needed. Each test builds its own tiny fixture
via nwis.db writers against a tmp sqlite since data/nwis.sqlite is populated
by a separate, independently-run synth generator (nwis.synth.field).
"""
from __future__ import annotations

import math

import pytest

from nwis import db, schema as S
from nwis.geo import correlate, dip, panel, trajectory
from nwis.geo.distance import bearing_deg, haversine_km
from nwis.geo.nearby import nearby_wells

EARTH_RADIUS_KM = 6371.0088
KM_PER_DEG_LAT = 111.32


# --------------------------------------------------------------------------- #
# distance.py
# --------------------------------------------------------------------------- #
def test_haversine_known_pair():
    # 1 degree of latitude along a meridian: great-circle distance = R * dphi exactly.
    expected = EARTH_RADIUS_KM * math.radians(1.0)
    assert haversine_km(0.0, 0.0, 1.0, 0.0) == pytest.approx(expected, rel=1e-6)
    assert haversine_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-9)


def test_bearing_known_pair():
    assert bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=1e-6)      # due north
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=1e-6)     # due east
    assert bearing_deg(1.0, 0.0, 0.0, 0.0) == pytest.approx(180.0, abs=1e-6)    # due south


# --------------------------------------------------------------------------- #
# trajectory.py
# --------------------------------------------------------------------------- #
def test_minimum_curvature_vertical_well_tvd_equals_md():
    table = trajectory.minimum_curvature([(0, 0, 0), (1000, 0, 0), (2000, 0, 0)])
    assert table["tvd"].iloc[-1] == pytest.approx(table["md"].iloc[-1], abs=1e-9)
    assert table["dls"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_minimum_curvature_30deg_hold_tvd_less_than_md():
    stations = [(0, 0, 0), (500, 0, 0), (900, 30, 90), (2000, 30, 90)]
    table = trajectory.minimum_curvature(stations)
    assert table["tvd"].iloc[-1] < table["md"].iloc[-1]
    # a 30 deg hold section (build already complete) should show ~zero dogleg severity
    assert table["dls"].iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_md_to_tvd_vertical_fallback_when_no_surveys(tmp_path):
    db.reset(tmp_path / "traj_fallback.sqlite")
    assert trajectory.md_to_tvd("NO-SUCH-WELL", 1234.5) == pytest.approx(1234.5)
    assert trajectory.tvd_to_md("NO-SUCH-WELL", 1234.5) == pytest.approx(1234.5)


def test_md_to_tvd_db_backed_roundtrip(tmp_path):
    db.reset(tmp_path / "traj_db.sqlite")
    db.add_surveys([
        S.SurveyStation(well_id="W1", md_m=0, inc_deg=0, azi_deg=0),
        S.SurveyStation(well_id="W1", md_m=500, inc_deg=0, azi_deg=0),
        S.SurveyStation(well_id="W1", md_m=900, inc_deg=30, azi_deg=90),
        S.SurveyStation(well_id="W1", md_m=2000, inc_deg=30, azi_deg=90),
    ])
    tvd_2000 = trajectory.md_to_tvd("W1", 2000)
    assert tvd_2000 < 2000  # deviated -> tvd strictly less than md past the build
    md_back = trajectory.tvd_to_md("W1", tvd_2000)
    assert md_back == pytest.approx(2000, abs=0.5)


# --------------------------------------------------------------------------- #
# dip.py — synthetic planar surface
# --------------------------------------------------------------------------- #
def _ll_from_local(lat0: float, lon0: float, east_km: float, north_km: float) -> tuple[float, float]:
    lat = lat0 + north_km / KM_PER_DEG_LAT
    lon = lon0 + east_km / (KM_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return lat, lon


def test_dip_fit_recovers_theta_phi_and_predicts_holdout(tmp_path):
    db.reset(tmp_path / "dip.sqlite")

    lat0, lon0 = 27.40, 95.30
    theta_true, phi_true, tvd0 = 3.0, 60.0, 2000.0
    b = math.tan(math.radians(theta_true)) * 1000.0 * math.sin(math.radians(phi_true))  # m per km east
    c = math.tan(math.radians(theta_true)) * 1000.0 * math.cos(math.radians(phi_true))  # m per km north

    def top_tvd_at(east_km: float, north_km: float) -> float:
        return tvd0 + b * east_km + c * north_km

    fit_points = [(0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (-2.0, 1.0), (1.0, -2.0)]
    wells = []
    for i, (e, n) in enumerate(fit_points, start=1):
        lat, lon = _ll_from_local(lat0, lon0, e, n)
        wid = f"FIT-{i}"
        wells.append(S.Well(well_id=wid, name=wid, field="Duliajan", lat=lat, lon=lon, td_md_m=3500))
        db.add_tops([S.FormationTop(
            well_id=wid, formation="Tipam",
            top_md_m=top_tvd_at(e, n), top_tvd_m=top_tvd_at(e, n),
        )])
    db.upsert_wells(wells)

    theta_fit, phi_fit, rmse, n = dip.fit_dip("Tipam")
    assert n == 5
    assert rmse == pytest.approx(0.0, abs=1e-3)
    assert theta_fit == pytest.approx(theta_true, abs=0.5)
    assert phi_fit == pytest.approx(phi_true, abs=10.0)

    # Held-out well: NOT written to formation_tops, only to wells.
    e_ho, n_ho = 3.0, 3.0
    lat_ho, lon_ho = _ll_from_local(lat0, lon0, e_ho, n_ho)
    db.upsert_wells([S.Well(well_id="HOLDOUT", name="HOLDOUT", field="Duliajan",
                             lat=lat_ho, lon=lon_ho, td_md_m=3500)])

    true_top = top_tvd_at(e_ho, n_ho)
    tvd_expected, n_used, method = dip.predict_top_at("HOLDOUT", "Tipam")
    assert n_used == 5
    assert method == "dip_projection_fit"
    assert tvd_expected == pytest.approx(true_top, abs=15.0)


def test_dip_fit_falls_back_to_defaults_below_min_wells(tmp_path):
    db.reset(tmp_path / "dip_default.sqlite")
    db.upsert_wells([
        S.Well(well_id="A", name="A", field="Duliajan", lat=27.40, lon=95.30, td_md_m=3000),
        S.Well(well_id="B", name="B", field="Duliajan", lat=27.41, lon=95.31, td_md_m=3000),
    ])
    db.add_tops([S.FormationTop(well_id="B", formation="Kopili", top_md_m=2800, top_tvd_m=2800)])

    theta, phi, rmse, n = dip.fit_dip("Kopili")
    assert n == 1
    assert theta == dip.DEFAULT_DIP_THETA_DEG
    assert phi == dip.DEFAULT_DIP_AZIMUTH_DEG
    assert math.isnan(rmse)

    tvd_expected, n_used, method = dip.predict_top_at("A", "Kopili")
    assert n_used == 1
    assert method == "dip_projection_default"
    assert tvd_expected is not None


# --------------------------------------------------------------------------- #
# nearby.py — honest degradation (A1/A11)
# --------------------------------------------------------------------------- #
@pytest.fixture
def six_well_db(tmp_path):
    db.reset(tmp_path / "nearby.sqlite")
    wells = [
        S.Well(well_id="A", name="A", field="Duliajan", lat=27.40, lon=95.30,
               td_md_m=3000, trajectory_type=S.TrajectoryType.vertical),
        S.Well(well_id="B1", name="B1", field="Duliajan", lat=27.41, lon=95.30,
               td_md_m=3050, trajectory_type=S.TrajectoryType.vertical),
        S.Well(well_id="B2", name="B2", field="Duliajan", lat=27.40, lon=95.33,
               td_md_m=2900, trajectory_type=S.TrajectoryType.deviated),
        S.Well(well_id="B3", name="B3", field="Duliajan", lat=27.435, lon=95.30,
               td_md_m=3200, trajectory_type=S.TrajectoryType.vertical),
        S.Well(well_id="B4", name="B4", field="Duliajan", lat=27.90, lon=95.90,
               td_md_m=4000, trajectory_type=S.TrajectoryType.vertical),
    ]
    db.upsert_wells(wells)
    db.add_tops([
        S.FormationTop(well_id="A", formation="Tipam", top_md_m=2000, top_tvd_m=2000,
                       base_md_m=2300, base_tvd_m=2300),
        S.FormationTop(well_id="A", formation="Girujan", top_md_m=1500, top_tvd_m=1500,
                       base_md_m=2000, base_tvd_m=2000),
        S.FormationTop(well_id="B1", formation="Tipam", top_md_m=2050, top_tvd_m=2050),
        S.FormationTop(well_id="B1", formation="Girujan", top_md_m=1550, top_tvd_m=1550),
        S.FormationTop(well_id="B2", formation="Tipam", top_md_m=1950, top_tvd_m=1950),
        S.FormationTop(well_id="B3", formation="Kopili", top_md_m=2850, top_tvd_m=2850),
    ])
    db.upsert_events([S.DrillingEvent(
        event_id="ev-b1-1", well_id="B1", source_document_id="d1", source_page_ref="d1#p1",
        event_type=S.EventType.mud_loss, depth_md_m=2100, depth_tvd_m=2100,
        formation="Tipam", severity=S.Severity.medium, hours_lost_npt=3.0,
        cause="losses on connection", remedy="LCM pill",
    )])
    return wells


def test_nearby_honest_degradation_renormalises_weights(six_well_db):
    candidates = nearby_wells("A", radius_km=5.0)
    ids = {c.well_id for c in candidates}
    assert ids == {"B1", "B2", "B3"}   # B4 (~far away) excluded by radius

    for c in candidates:
        assert "difficulty" in c.dimensions_unavailable
        assert "difficulty" not in c.dimensions
        assert sum(c.weights_used.values()) == pytest.approx(1.0, abs=1e-9)

    b1 = next(c for c in candidates if c.well_id == "B1")
    assert b1.dimensions["formation_overlap"] == pytest.approx(1.0)  # full overlap with A
    b3 = next(c for c in candidates if c.well_id == "B3")
    assert b3.dimensions["formation_overlap"] == pytest.approx(0.0)  # no shared formation

    # Full-overlap, same-trajectory, closer well should outrank the no-overlap one.
    assert b1.score > b3.score


# --------------------------------------------------------------------------- #
# correlate.py
# --------------------------------------------------------------------------- #
def test_formation_offset(six_well_db):
    formation, offset = correlate.formation_offset("B1", 2100)
    assert formation == "Tipam"
    assert offset == pytest.approx(50.0)

    formation, offset = correlate.formation_offset("B1", 100)
    assert formation is None and offset is None


def test_correlation_panel_data_shapes(six_well_db):
    panel_data = correlate.correlation_panel_data(["A", "B1"])
    assert set(panel_data["wells"].keys()) == {"A", "B1"}
    assert panel_data["wells"]["A"]["bands"][0]["formation"] in ("Tipam", "Girujan")
    assert len(panel_data["wells"]["B1"]["events"]) == 1
    assert "Tipam" in panel_data["normalised"]
    assert "B1" in panel_data["normalised"]["Tipam"]
    b1_events = panel_data["normalised"]["Tipam"]["B1"]["events"]
    assert b1_events[0]["offset_from_top_m"] == pytest.approx(50.0)


def test_lookahead_returns_only_events_in_upcoming_interval(six_well_db):
    hits_narrow = correlate.active_well_lookahead("A", current_md=2000, lookahead_m=150, radius_km=5.0)
    assert len(hits_narrow) == 1
    assert hits_narrow[0]["formation"] == "Tipam"
    assert hits_narrow[0]["source_well_id"] == "B1"
    assert 2000 <= hits_narrow[0]["expected_md_m"] <= 2150

    # A far-future window should catch nothing (event maps near ~2000-2100, not near 9000+).
    hits_far = correlate.active_well_lookahead("A", current_md=9000, lookahead_m=50, radius_km=5.0)
    assert hits_far == []


# --------------------------------------------------------------------------- #
# panel.py — figures build without error
# --------------------------------------------------------------------------- #
def test_correlation_figure_builds_both_modes(six_well_db):
    panel_data = correlate.correlation_panel_data(["A", "B1", "B2"])
    fig_tvd = panel.correlation_figure(panel_data, mode="tvd")
    fig_norm = panel.correlation_figure(panel_data, mode="normalised")
    assert len(fig_tvd.data) >= 1
    assert len(fig_norm.data) >= 1


def test_map_figure_builds(six_well_db):
    wells = db.all_wells()
    candidates = nearby_wells("A", radius_km=5.0)
    fig = panel.map_figure(wells, active_id="A", radius_km=5.0, candidates=candidates)
    assert len(fig.data) >= 2  # wells trace + radius-circle trace
