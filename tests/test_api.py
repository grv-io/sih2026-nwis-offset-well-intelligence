"""Smoke tests for api/main.py using httpx's TestClient (via FastAPI's
TestClient wrapper) against a tiny fixture db. No LLM/network needed -
nwis.llm.health() degrades to {"ollama": False, ...} when Ollama isn't
reachable, which /health surfaces rather than hiding.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from nwis import db, schema as S


def _seed(tmp_path):
    db.reset(tmp_path / "api.sqlite")
    db.upsert_wells([
        S.Well(well_id="A", name="A", field="Duliajan", lat=27.40, lon=95.30, td_md_m=3000),
        S.Well(well_id="B1", name="B1", field="Duliajan", lat=27.41, lon=95.30, td_md_m=3050),
    ])
    db.add_tops([
        S.FormationTop(well_id="A", formation="Tipam", top_md_m=2000, top_tvd_m=2000),
        S.FormationTop(well_id="B1", formation="Tipam", top_md_m=2050, top_tvd_m=2050),
    ])
    db.upsert_events([S.DrillingEvent(
        event_id="ev1", well_id="B1", source_document_id="d1", source_page_ref="d1#p1",
        event_type=S.EventType.mud_loss, depth_md_m=2100, depth_tvd_m=2100, formation="Tipam",
    )])


def test_api_smoke(tmp_path):
    _seed(tmp_path)
    from api.main import app
    client = TestClient(app)

    r = client.get("/health")
    assert r.status_code == 200
    assert "llm" in r.json()

    r = client.get("/wells")
    assert r.status_code == 200
    assert {w["well_id"] for w in r.json()} == {"A", "B1"}

    r = client.get("/wells/A")
    assert r.status_code == 200
    assert r.json()["well_id"] == "A"

    r = client.get("/wells/NO-SUCH")
    assert r.status_code == 404

    r = client.get("/nearby", params={"well_id": "A", "radius_km": 5.0})
    assert r.status_code == 200
    assert any(c["well_id"] == "B1" for c in r.json())

    r = client.get("/correlation", params={"well_ids": "A,B1", "mode": "tvd"})
    assert r.status_code == 200
    body = r.json()
    assert "panel_data" in body and "figure" in body

    r = client.get("/lookahead", params={"well_id": "A", "md": 2000, "lookahead_m": 150, "radius_km": 5.0})
    assert r.status_code == 200

    r = client.get("/dip/Tipam")
    assert r.status_code == 200
    assert "theta_deg" in r.json()
