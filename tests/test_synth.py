"""Tests for nwis/synth/ (Phase 1: synthetic Upper Assam field)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from nwis.schema import FORMATIONS

SEED = 7


@pytest.fixture(scope="session")
def synth_out(tmp_path_factory):
    from nwis.synth import field as field_mod
    out = tmp_path_factory.mktemp("synth_out")
    db_path = out / "test_nwis.sqlite"
    # Isolated DB path: avoids colliding with the shared project data/nwis.sqlite, which
    # another Phase-2 (ingestion) process may hold open concurrently.
    field_mod.main(["--seed", str(SEED), "--out", str(out), "--db-path", str(db_path)])
    return out


@pytest.fixture(scope="session")
def wells_df(synth_out):
    return pd.read_csv(synth_out / "wells.csv")


@pytest.fixture(scope="session")
def tops_df(synth_out):
    return pd.read_csv(synth_out / "tops.csv")


@pytest.fixture(scope="session")
def surveys_df(synth_out):
    return pd.read_csv(synth_out / "surveys.csv")


@pytest.fixture(scope="session")
def events(synth_out):
    return json.loads((synth_out / "events_truth.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def documents(synth_out):
    return json.loads((synth_out / "documents.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def hot_zones(synth_out):
    return json.loads((synth_out / "hot_zones.json").read_text(encoding="utf-8"))


def test_30_wells(wells_df):
    assert len(wells_df) == 30
    assert set(wells_df["field"].value_counts().to_dict().items()) == {
        ("Duliajan", 12), ("Moran", 10), ("Naharkatiya", 8),
    }


def test_tops_monotonic_and_canonical_order(tops_df):
    for well_id, grp in tops_df.groupby("well_id"):
        grp = grp.sort_values("top_md_m")
        assert list(grp["formation"]) == [f for f in FORMATIONS if f in set(grp["formation"])]
        assert grp["top_md_m"].is_monotonic_increasing
        assert grp["top_tvd_m"].is_monotonic_increasing


def test_event_formation_matches_tops_at_depth(events, tops_df):
    by_well = {wid: g.sort_values("top_md_m").reset_index(drop=True) for wid, g in tops_df.groupby("well_id")}
    for e in events:
        tops = by_well[e["well_id"]]
        depth = e["depth_md_m"]
        row = tops[tops["formation"] == e["formation"]].iloc[0]
        assert row["top_md_m"] - 1e-3 <= depth <= row["base_md_m"] + 1e-3, (
            f"{e['event_id']}: depth {depth} not within {e['formation']} "
            f"[{row['top_md_m']}, {row['base_md_m']}]"
        )


def test_deviated_wells_tvd_less_than_md_at_td(wells_df, tops_df):
    deviated = wells_df[wells_df["trajectory_type"] == "deviated"]
    assert len(deviated) > 0
    basement = tops_df[tops_df["formation"] == "Basement"].set_index("well_id")
    for _, w in deviated.iterrows():
        row = basement.loc[w["well_id"]]
        assert row["base_tvd_m"] < row["base_md_m"], (
            f"{w['well_id']}: deviated well but TVD {row['base_tvd_m']} not < MD {row['base_md_m']} at TD"
        )


def test_hot_zone_shared_by_at_least_three_wells(wells_df, events, hot_zones):
    def haversine_km(lat1, lon1, lat2, lon2):
        r = 6371.0
        p1, p2 = np.radians(lat1), np.radians(lat2)
        dphi = np.radians(lat2 - lat1)
        dlmb = np.radians(lon2 - lon1)
        a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
        return 2 * r * np.arcsin(np.sqrt(a))

    events_by_well: dict[str, set[str]] = {}
    for e in events:
        events_by_well.setdefault(e["well_id"], set()).add(e["event_type"])

    found = False
    for hz in hot_zones:
        wells_in_zone = [
            w["well_id"] for _, w in wells_df.iterrows()
            if haversine_km(w["lat"], w["lon"], hz["lat"], hz["lon"]) <= hz["radius_km"]
        ]
        matching = [wid for wid in wells_in_zone if hz["hazard"] in events_by_well.get(wid, set())]
        if len(matching) >= 3:
            found = True
            break
    assert found, "no hot zone has >= 3 wells sharing the same hazard event type"


def test_scanned_pdfs_have_no_extractable_text(documents):
    import pdfplumber

    scanned_docs = [d for d in documents if d["is_scanned"]]
    assert len(scanned_docs) > 0
    checked = 0
    for d in scanned_docs[:15]:
        with pdfplumber.open(d["path"]) as pdf:
            text = "".join((p.extract_text() or "") for p in pdf.pages)
        assert text.strip() == "", f"{d['path']} unexpectedly has extractable text"
        checked += 1
    assert checked > 0


def test_non_scanned_pdfs_have_extractable_text(documents):
    import pdfplumber

    born_digital = [d for d in documents if not d["is_scanned"]]
    assert len(born_digital) > 0
    with pdfplumber.open(born_digital[0]["path"]) as pdf:
        text = "".join((p.extract_text() or "") for p in pdf.pages)
    assert text.strip() != ""


def test_log_precursor_before_stuck_pipe(synth_out, events):
    stuck_pipe_events = [e for e in events if e["event_type"] == "stuck_pipe"]
    assert len(stuck_pipe_events) > 0

    # "Baseline" = the same well/formation section immediately BEFORE the precursor ramp
    # starts (event_depth-200 .. event_depth-50), not the whole well's cross-formation
    # median -- deeper/harder formations have a naturally higher torque baseline, so a
    # global median is not a fair reference for an anomaly that is local to one section.
    checked = 0
    for e in stuck_pipe_events:
        path = synth_out / "logs" / f"{e['well_id']}.parquet"
        df = pd.read_parquet(path)
        depth = e["depth_md_m"]
        baseline = df[(df["depth_md_m"] >= depth - 200) & (df["depth_md_m"] < depth - 50)]
        window = df[(df["depth_md_m"] >= depth - 50) & (df["depth_md_m"] <= depth)]
        if baseline.empty or window.empty:
            continue
        baseline_median = baseline["torque_kftlb"].median()
        window_mean = window["torque_kftlb"].mean()
        assert window_mean >= baseline_median * 1.20, (
            f"{e['event_id']}: precursor window torque {window_mean:.2f} not >=20% over "
            f"local baseline median {baseline_median:.2f}"
        )
        checked += 1
    assert checked > 0


def test_ddr_pdf_count_within_target_budget(documents):
    ddr_count = sum(1 for d in documents if d["doc_type"] == "DDR")
    assert 100 <= ddr_count <= 300, f"DDR PDF count {ddr_count} far outside the ~150-250 target band"
