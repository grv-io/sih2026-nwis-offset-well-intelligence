"""Smoke tests for api/ui_router.py (Phase 7 web UI endpoints) against a tiny
fixture db in tmp_path. No LLM / network. Mirrors tests/test_api.py's pattern:
db.reset(<tmp path>) points the shared engine at a throwaway sqlite file.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nwis import db, schema as S


def _ev(event_id, well, conf=0.9, method=S.ExtractionMethod.ocr_llm_local, et=S.EventType.stuck_pipe,
        depth=2100.0, doc="DOC-B1", page="B1_DDR_2019-03-11.txt#p1", text="Pipe stuck at 2100 m, jarred free"):
    return S.DrillingEvent(
        event_id=event_id, well_id=well, source_document_id=doc, source_page_ref=page,
        event_type=et, depth_md_m=depth, depth_tvd_m=depth, formation="Tipam",
        extraction_confidence=conf, extraction_method=method, free_text=text,
        reviewed_by_human=method == S.ExtractionMethod.synthetic_truth,
    )


@pytest.fixture()
def client(tmp_path):
    db.reset(tmp_path / "ui.sqlite")
    db.upsert_wells([
        S.Well(well_id="A", name="A", field="Duliajan", lat=27.40, lon=95.30, td_md_m=3000),
        S.Well(well_id="B1", name="B1", field="Duliajan", lat=27.41, lon=95.30, td_md_m=3050),
    ])
    db.add_tops([
        S.FormationTop(well_id="A", formation="Girujan", top_md_m=1500, top_tvd_m=1500),
        S.FormationTop(well_id="A", formation="Tipam", top_md_m=2000, top_tvd_m=2000),
        S.FormationTop(well_id="B1", formation="Girujan", top_md_m=1550, top_tvd_m=1550),
        S.FormationTop(well_id="B1", formation="Tipam", top_md_m=2050, top_tvd_m=2050),
    ])
    txt = tmp_path / "B1_DDR_2019-03-11.txt"
    txt.write_text("DAILY DRILLING REPORT\nWell: B1\n06:00 Pipe stuck at 2100 m, jarred free after 4 hrs.\n", encoding="utf-8")
    db.upsert_documents([
        S.SourceDocument(document_id="DOC-B1", well_id="B1", doc_type="DDR", path=str(txt),
                         report_date=date(2019, 3, 11)),
        S.SourceDocument(document_id="DOC-ING", well_id="B1", doc_type="DDR",
                         path=str(tmp_path / "B1_DDR_2019-03-12.pdf"), report_date=date(2019, 3, 12)),
    ])
    db.add_chunks([db.ChunkRow(chunk_id="c1", document_id="DOC-ING", well_id="B1",
                               page_ref="B1_DDR_2019-03-12.pdf#p1",
                               text="Tipam losses 40 bbl/hr, pumped LCM pill, regained returns.")])
    db.upsert_events([
        _ev("ext-hi", "B1", conf=0.9),
        _ev("ext-lo", "B1", conf=0.4, et=S.EventType.mud_loss, depth=2150, doc="DOC-ING",
            page="B1_DDR_2019-03-12.pdf#p1", text="Tipam losses 40 bbl/hr"),
        _ev("truth-1", "B1", method=S.ExtractionMethod.synthetic_truth, conf=1.0),
    ])
    from api.main import app
    return TestClient(app)


def test_events_source_filter(client):
    ids = lambda r: {e["event_id"] for e in r.json()}  # noqa: E731
    r = client.get("/events", params={"well_ids": "B1"})
    assert r.status_code == 200 and ids(r) == {"ext-hi", "ext-lo"}  # default = extracted
    assert ids(client.get("/events", params={"source": "truth"})) == {"truth-1"}
    assert ids(client.get("/events", params={"source": "all"})) == {"ext-hi", "ext-lo", "truth-1"}
    assert ids(client.get("/events", params={"source": "all", "ids": "truth-1,ext-hi"})) == {"truth-1", "ext-hi"}
    assert ids(client.get("/events", params={"event_type": "mud_loss"})) == {"ext-lo"}
    assert client.get("/events", params={"source": "bogus"}).status_code == 422


def test_ui_summary(client):
    r = client.get("/ui/summary")
    assert r.status_code == 200
    j = r.json()
    assert j["wells"] == 2 and j["events_truth"] == 1 and j["events_extracted"] == 2 and j["chunks"] == 1


def test_review_queue_approve_reject(client):
    q = client.get("/review-queue").json()
    assert [i["event_id"] for i in q["items"]] == ["ext-lo"]
    assert q["counters"] == {"extracted": 2, "reviewed": 0, "pending": 1}

    assert client.post("/review-queue/ext-lo/approve").status_code == 200
    q = client.get("/review-queue").json()
    assert q["counters"] == {"extracted": 2, "reviewed": 1, "pending": 0}

    assert client.post("/review-queue/ext-hi/reject").status_code == 200
    assert client.get("/review-queue").json()["counters"]["extracted"] == 1
    assert client.post("/review-queue/truth-1/reject").status_code == 409  # ground truth is immutable
    assert client.post("/review-queue/nope/approve").status_code == 404


def test_documents_text_and_resolve(client):
    r = client.get("/documents/DOC-B1/text")
    assert r.status_code == 200
    assert "jarred free" in r.json()["text"] and r.json()["text_source"] == "report_text"

    r = client.get("/documents/DOC-ING/text", params={"page": 1})
    assert r.json()["text_source"] == "ingested_chunks" and "LCM" in r.json()["text"]

    r = client.get("/documents/resolve", params={"ref": "[B1 | B1_DDR_2019-03-11.txt#p1]"})
    assert r.status_code == 200 and r.json()["document_id"] == "DOC-B1"
    r = client.get("/documents/resolve", params={"ref": 'B1 | DOC-ING | B1_DDR_2019-03-12.pdf#p1 — "Tipam losses"'})
    assert r.status_code == 200 and r.json()["document_id"] == "DOC-ING"
    assert client.get("/documents/resolve", params={"ref": "B1 live sensors @ t=5s"}).status_code == 404
    assert client.get("/documents/NOPE/text").status_code == 404


def test_correlation_figure_has_bands_and_respects_source(client):
    r = client.get("/correlation/figure", params={"well_ids": "A,B1", "mode": "tvd"})
    assert r.status_code == 200
    j = r.json()
    assert j["n_events"] == 2  # extracted only
    assert len(j["figure"]["layout"]["shapes"]) >= 4  # formation bands present for both wells
    j = client.get("/correlation/figure", params={"well_ids": "A,B1", "mode": "normalised", "source": "truth"}).json()
    assert j["n_events"] == 1
    assert client.get("/correlation/figure", params={"well_ids": ""}).status_code == 400


def test_map_data(client):
    r = client.get("/map/data", params={"well_id": "A", "radius_km": 5})
    assert r.status_code == 200
    j = r.json()
    b1 = next(w for w in j["wells"] if w["well_id"] == "B1")
    assert b1["n_events"] == 2 and b1["dominant_family"] in {"stuck", "losses"}
    assert [c["well_id"] for c in j["candidates"]] == ["B1"]
    assert "dimensions_unavailable" in j["candidates"][0]
    assert len(j["ring"]) > 10
    assert client.get("/map/data", params={"well_id": "NOPE"}).status_code == 404


def test_keyword_search(client):
    r = client.get("/search/keyword", params={"q": "Tipam LCM"})
    assert r.status_code == 200
    assert r.json()[0]["chunk_id"] == "c1" and r.json()[0]["document_id"] == "DOC-ING"


_VOLVE_DB = Path(__file__).resolve().parents[1] / "data" / "volve.sqlite"


@pytest.mark.skipif(not _VOLVE_DB.exists(), reason="data/volve.sqlite not built (run nwis.external.volve.wells)")
def test_map_data_volve_basin(client):
    # basin=volve reads data/volve.sqlite directly, ignoring the fixture db the
    # `client` fixture just pointed the shared engine at.
    r = client.get("/map/data", params={"basin": "volve"})
    assert r.status_code == 200
    j = r.json()
    assert len(j["wells"]) > 0
    assert all(w["field"] == "Volve" for w in j["wells"])
    assert j["candidates"] == []
    assert j["note"] == "similarity ranking needs formation tops; not available for Volve"

    first_well = j["wells"][0]["well_id"]
    r2 = client.get("/map/data", params={"basin": "volve", "well_id": first_well, "radius_km": 5})
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["active_well_id"] == first_well
    assert j2["candidates"] == []
    assert len(j2["ring"]) > 10

    assert client.get("/map/data", params={"basin": "volve", "well_id": "NOT-A-WELL"}).status_code == 404


@pytest.mark.skipif(not _VOLVE_DB.exists(), reason="data/volve.sqlite not built (run nwis.external.volve.wells)")
def test_events_volve_basin(client):
    r = client.get("/events", params={"basin": "volve", "source": "all"})
    assert r.status_code == 200
    events = r.json()
    assert len(events) > 0
    assert all(e["formation"] is None for e in events)  # Phase 8: Volve tops not loaded
    assert all(e["is_ground_truth"] is False for e in events)  # no synthetic_truth rows in Volve


def test_map_data_volve_missing_db(client, monkeypatch):
    import api.ui_router as ui_router

    monkeypatch.setattr(ui_router, "VOLVE_DB_PATH", Path("Z:/does/not/exist/volve.sqlite"))
    r = client.get("/map/data", params={"basin": "volve"})
    assert r.status_code == 404
    assert "volve.sqlite" in r.json()["detail"]


def test_api_prefix_and_live_state(client):
    assert client.get("/api/events").status_code == 200  # /api/* prefix stripped for the SPA

    from api import live_router

    live_router._session = None
    assert client.get("/ui/live-state").status_code == 409
    try:
        r = client.post("/ui/live/start", json={"well_id": "A", "speed": 50, "start_md": 2100})
        assert r.status_code == 200
        s = client.get("/ui/live-state", params={"window": 10}).json()
        assert s["well_id"] == "A" and s["depth_md_m"] is not None and abs(s["depth_md_m"] - 2100) < 5
        assert isinstance(s["window"], list) and "running" in s
        assert client.post("/ui/live/start", json={"well_id": "NOPE"}).status_code == 404
    finally:
        if live_router._session is not None:
            live_router._session.stop()
        live_router._session = None
