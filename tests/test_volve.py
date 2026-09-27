"""Tests for nwis/external/volve/ (Phase 8). No network, no LLM -- these run against
small in-memory/hand-built frames and a tmp_path, never against the real download.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from nwis import schema as S
from nwis.external.volve import labels as L
from nwis.external.volve import prepare_docs as PD
from nwis.external.volve import wells as W


# --------------------------------------------------------------------------- #
# prepare_docs.py
# --------------------------------------------------------------------------- #
def _fake_ddr_rows() -> pd.DataFrame:
    """5 rows: 3 report-days for well A, 2 for well B -- enough to exercise
    day-numbering, running depth, and per-well grouping in one frame."""
    rows = [
        {
            "well_id": "15_9-F-99", "nameWellbore": "NO 15/9-F-99", "report_date": "2007-01-01",
            "statusInfo": [{"md": "100"}],
            "activity": [{"comments": "Drilled ahead, no problems.", "dTimStart": "2007-01-01T00:00:00"}],
        },
        {
            "well_id": "15_9-F-99", "nameWellbore": "NO 15/9-F-99", "report_date": "2007-01-02",
            "statusInfo": [{"md": "180"}],
            "activity": [
                {"comments": "Continued drilling.  Extra   spaces.", "dTimStart": "2007-01-02T06:00:00"},
                {"comments": "Circulated bottoms up.", "dTimStart": "2007-01-02T00:00:00"},
            ],
        },
        {
            "well_id": "15_9-F-99", "nameWellbore": "NO 15/9-F-99", "report_date": "2007-01-03",
            "statusInfo": [{"md": "-999.99"}],  # sentinel -> unresolved end depth
            "activity": [],
        },
        {
            "well_id": "15_9-F-77", "nameWellbore": "NO 15/9-F-77", "report_date": "2008-05-10",
            "statusInfo": [{"md": "50"}],
            "activity": [{"comments": "Rigged up.", "dTimStart": "2008-05-10T00:00:00"}],
        },
        {
            "well_id": "15_9-F-77", "nameWellbore": "NO 15/9-F-77", "report_date": "2008-05-11",
            "statusInfo": [{"md": "95"}],
            "activity": [{"comments": "Drilled to 95 m MD.", "dTimStart": "2008-05-11T00:00:00"}],
        },
    ]
    return pd.DataFrame(rows)


def test_build_documents_writes_correctly_named_files_and_manifest(tmp_path: Path):
    f = _fake_ddr_rows()
    docs_dir = tmp_path / "docs"
    manifest, summary = PD.build_documents(f, ["15_9-F-99", "15_9-F-77"], docs_dir)

    assert len(manifest) == 5
    assert (docs_dir / "15_9-F-99" / "DDR_2007-01-01.txt").exists()
    assert (docs_dir / "15_9-F-99" / "DDR_2007-01-02.txt").exists()
    assert (docs_dir / "15_9-F-77" / "DDR_2008-05-11.txt").exists()

    entry = next(e for e in manifest if e["report_date"] == "2007-01-02")
    assert entry["well_id"] == "15_9-F-99"
    assert entry["doc_type"] == "DDR"
    assert entry["path"] == "docs/15_9-F-99/DDR_2007-01-02.txt"

    text = (docs_dir / "15_9-F-99" / "DDR_2007-01-02.txt").read_text(encoding="utf-8")
    assert "Well: 15_9-F-99" in text
    assert "Report Date: 2007-01-02   Report Day: 2" in text
    assert "Depth: 100 -> 180 m MD  (progress 80 m)" in text
    assert "Continued drilling. Extra spaces." in text  # whitespace collapsed
    assert "Circulated bottoms up." in text

    # day 3's sentinel end-depth resolves to "?" rather than a fabricated number,
    # and running depth correctly carries over from day 2 (180), not day 3's missing value
    day3 = (docs_dir / "15_9-F-99" / "DDR_2007-01-03.txt").read_text(encoding="utf-8")
    assert "Depth: 180 -> ? m MD" in day3

    assert summary["15_9-F-99"]["n_days"] == 3
    assert summary["15_9-F-77"]["n_days"] == 2

    manifest_path = tmp_path / "documents.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(reloaded) == 5


def test_top_wellbores_orders_by_report_day_count():
    f = _fake_ddr_rows()
    assert PD.top_wellbores(f, top_n=1) == ["15_9-F-99"]  # 3 days beats 2


# --------------------------------------------------------------------------- #
# labels.py
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line,expected_type", [
    ("Observed losses to formation while drilling, pumped LCM pill.", "mud_loss"),
    ("String got stuck while POOH, worked free after jarring down.", "stuck_pipe"),
    ("Well flowed back, gas influx observed, shut in for kick.", "kick"),
    ("Cement squeeze failed to hold pressure, channelling suspected behind casing.", "cementing_issue"),
    ("Ran fishing assembly to retrieve junk left in hole.", "fishing_operation"),
    ("Tight hole on trip out, hole caving in Hordaland shale section.", "wellbore_instability"),
])
def test_label_line_hits_expected_event_type(line, expected_type):
    hits = L.label_line(line)
    assert expected_type in hits


def test_label_line_does_not_fire_on_routine_line():
    routine = "Drilled ahead 2510-2565m, no problems encountered. Circulated bottoms up, POOH, RIH, resumed drilling."
    assert L.label_line(routine) == []


def test_explicit_depth_extracted_when_present():
    assert L._explicit_depth("Lost circulation observed at 2460 m MD.") == 2460.0
    assert L._explicit_depth("No depth mentioned here.") is None


def test_parse_ddr_txt_and_build_weak_truth_roundtrip(tmp_path: Path):
    f = _fake_ddr_rows()
    docs_dir = tmp_path / "docs"
    PD.build_documents(f, ["15_9-F-99"], docs_dir)

    # inject one hazard line into day 1's doc so build_weak_truth has something to find
    day1 = docs_dir / "15_9-F-99" / "DDR_2007-01-01.txt"
    text = day1.read_text(encoding="utf-8")
    text = text.replace(
        "Drilled ahead, no problems.",
        "Drilled ahead, no problems.\n  Observed stuck pipe while tripping out, worked free with jar.",
    )
    day1.write_text(text, encoding="utf-8")

    parsed = L.parse_ddr_txt(day1)
    assert parsed["well_id"] == "15_9-F-99"
    assert parsed["report_date"] == "2007-01-01"
    assert parsed["end_md_m"] == 100.0
    assert any("stuck pipe" in line for line in parsed["lines"])

    truth = L.build_weak_truth(docs_dir)
    stuck_events = [t for t in truth if t["event_type"] == "stuck_pipe"]
    assert len(stuck_events) >= 1
    assert stuck_events[0]["well_id"] == "15_9-F-99"
    assert stuck_events[0]["report_date"] == "2007-01-01"
    assert stuck_events[0]["depth_md_m"] == 100.0  # fallback to day's end depth, no explicit depth in line
    assert stuck_events[0]["formation"] is None
    assert stuck_events[0]["extraction_method"] == "weak_regex_label"


# --------------------------------------------------------------------------- #
# wells.py
# --------------------------------------------------------------------------- #
def _fake_ddr_index() -> pd.DataFrame:
    return pd.DataFrame([
        {"well_id": "15_9-F-1", "nameWellbore": "NO 15/9-F-1", "npd_number": 7223},
        {"well_id": "15_9-F-2", "nameWellbore": "NO 15/9-F-2", "npd_number": 7224},
    ])


def _fake_dev_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "wlbNpdidWellbore": 7223, "wlbNsDecDeg": 58.4416, "wlbEwDecDeg": 1.8874,
            "wlbKellyBushElevation": 54.9, "wlbTotalDepth": 3632.0, "wlbFinalVerticalDepth": 3328.0,
            "wlbStatus": "P&A",
        },
        {
            "wlbNpdidWellbore": 7224, "wlbNsDecDeg": 58.4417, "wlbEwDecDeg": 1.8875,
            "wlbKellyBushElevation": 54.9, "wlbTotalDepth": float("nan"), "wlbFinalVerticalDepth": float("nan"),
            "wlbStatus": "Producing",
        },
    ])


def test_sanitize_wellbore_id():
    assert W.sanitize_wellbore_id("NO 15/9-F-11 T2") == "15_9-F-11-T2"
    assert W.sanitize_wellbore_id("15/9-F-1") == "15_9-F-1"


def test_parse_md_filters_sentinels():
    assert W._parse_md("-999.99") is None
    assert W._parse_md("-9999") is None
    assert W._parse_md("258") == 258.0
    assert W._parse_md(None) is None
    assert W._parse_md("not_a_number") is None


def test_build_wells_from_fake_factpages_frame_no_surveys():
    ddr_index = _fake_ddr_index()
    dev_df = _fake_dev_frame()

    wells, vertical_fallback = W.build_wells(ddr_index, dev_df, surveys_by_well={})

    assert len(wells) == 2
    assert set(vertical_fallback) == {"15_9-F-1", "15_9-F-2"}  # no surveys given -> both fall back

    by_id = {w.well_id: w for w in wells}
    w1 = by_id["15_9-F-1"]
    assert isinstance(w1, S.Well)
    assert w1.field == "Volve"
    assert w1.basin == "North Sea (Volve)"
    assert w1.lat == pytest.approx(58.4416)
    assert w1.lon == pytest.approx(1.8874)
    assert w1.td_md_m == pytest.approx(3632.0)
    assert w1.trajectory_type == S.TrajectoryType.vertical
    assert w1.status == "p&a"

    # well 2 has NaN total/vertical depth in Sodir and no surveys -> honest 0.0, not a guess
    w2 = by_id["15_9-F-2"]
    assert w2.td_md_m == 0.0
    assert w2.status == "producing"


def test_build_wells_skips_wellbore_with_no_npd_match():
    ddr_index = pd.DataFrame([{"well_id": "15_9-F-1", "nameWellbore": "NO 15/9-F-1", "npd_number": 99999}])
    dev_df = _fake_dev_frame()
    wells, _ = W.build_wells(ddr_index, dev_df, surveys_by_well={})
    assert wells == []


def test_build_wells_uses_deviated_trajectory_when_survey_present():
    ddr_index = pd.DataFrame([{"well_id": "15_9-F-1", "nameWellbore": "NO 15/9-F-1", "npd_number": 7223}])
    dev_df = _fake_dev_frame()
    surveys = {
        "15_9-F-1": [
            S.SurveyStation(well_id="15_9-F-1", md_m=1000, inc_deg=0.5, azi_deg=10),
            S.SurveyStation(well_id="15_9-F-1", md_m=2000, inc_deg=25.0, azi_deg=10),
        ]
    }
    wells, vertical_fallback = W.build_wells(ddr_index, dev_df, surveys_by_well=surveys)
    assert vertical_fallback == []
    assert wells[0].trajectory_type == S.TrajectoryType.deviated


def test_npd_number_from_alias():
    alias = [{"name": "15/9-19 A", "namingSystem": "NPD code"}, {"name": "3145", "namingSystem": "NPD number"}]
    assert W.npd_number_from_alias(alias) == 3145
    assert W.npd_number_from_alias(None) is None
    assert W.npd_number_from_alias([{"name": "x", "namingSystem": "NPD code"}]) is None
