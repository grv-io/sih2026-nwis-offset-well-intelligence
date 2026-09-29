"""The deployment has no LLM server: search, answer and live recommendations must
degrade to keyword / structured / template output without raising."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from nwis import db, llm as llm_mod, schema as S
from nwis.live.recommend import recommend_for_alert
from nwis.search import answer as answer_mod
from nwis.search.retrieve import hybrid_search

PAGE = "DUL-001_DDR_2019-03-11.txt#p1"


def _boom(*a, **k):
    raise AssertionError("LLM must not be called while offline")


@pytest.fixture
def offline_db(tmp_path, monkeypatch):
    db.reset(tmp_path / "offline.sqlite")
    db.upsert_wells([S.Well(well_id="DUL-001", name="DUL-001", field="Duliajan", lat=27.3, lon=95.0, td_md_m=3000)])
    db.add_chunks([db.ChunkRow(
        chunk_id="c1", document_id="doc-c1", well_id="DUL-001", page_ref=PAGE,
        text="Drilling ahead in Girujan Clay at 2450m. Pipe got stuck while pulling out of hole; "
             "worked free after spotting pill and jarring. NPT 6 hrs.")])
    db.upsert_events([S.DrillingEvent(
        event_id="e1", well_id="DUL-001", source_document_id="doc-c1", source_page_ref=PAGE,
        event_type=S.EventType.stuck_pipe, depth_md_m=2450, formation="Girujan", hours_lost_npt=6,
        cause="stuck pulling out", remedy="spotted pill, jarred free", extraction_confidence=0.9)])
    monkeypatch.setattr(llm_mod, "available", lambda: False)
    monkeypatch.setattr(llm_mod, "embed", _boom)
    monkeypatch.setattr(llm_mod, "complete", _boom)
    return tmp_path


def test_search_is_keyword_only(offline_db):
    hits = hybrid_search("stuck pipe Girujan", k=5)
    assert hits and hits[0].well_id == "DUL-001"
    assert all(h.why == "keyword-only (embeddings offline)" for h in hits)


def test_search_survives_embed_failure(offline_db, monkeypatch):
    monkeypatch.setattr(llm_mod, "available", lambda: True)
    monkeypatch.setattr(llm_mod, "embed_available", lambda: True)
    monkeypatch.setattr(llm_mod, "embed", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
    hits = hybrid_search("stuck pipe Girujan", k=5)
    assert hits and hits[0].why == "keyword-only (embeddings offline)"


def test_answer_returns_cited_structured_fallback(offline_db):
    a = answer_mod.answer("Girujan stuck pipe remedy")
    assert a.degraded and not a.refused
    assert a.text.startswith(answer_mod.OFFLINE_PREFIX)
    assert "DUL-001" in a.text and a.citations


def test_answer_survives_complete_failure(offline_db, monkeypatch):
    monkeypatch.setattr(llm_mod, "available", lambda: True)
    monkeypatch.setattr(llm_mod, "complete", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    monkeypatch.setattr(llm_mod, "embed", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
    a = answer_mod.answer("Girujan stuck pipe remedy")
    assert a.degraded and a.text.startswith(answer_mod.OFFLINE_PREFIX)


def test_recommendation_uses_template(offline_db):
    alert = S.Alert(alert_id="a1", well_id="DUL-001", t_s=10.0, depth_md_m=2450.0, rule="precedent_zone",
                    hazard=S.EventType.stuck_pipe, severity=S.Severity.high, formation="Girujan",
                    message="torque ramp")
    out = recommend_for_alert(alert, [{"well_id": "DUL-001", "page_ref": PAGE, "remedy": "spotted pill, jarred free"}])
    assert "spotted pill" in out.recommendation and out.citations


def test_health_reports_offline_deployment(offline_db):
    from api.main import app
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    d = r.json()["deployment"]
    assert d["llm"] == "offline" and d["db_events"] == 1


def test_available_is_false_when_unreachable_and_cached(monkeypatch):
    monkeypatch.setattr(llm_mod.settings, "ollama_base_url", "http://127.0.0.1:9/v1", raising=False)
    monkeypatch.setattr(llm_mod, "_avail_cache", None)
    assert llm_mod.available() is False
    monkeypatch.setattr(llm_mod.httpx, "Client", _boom)  # a second call must hit the cache
    assert llm_mod.available() is False


def test_hosted_openai_compatible_server(offline_db, monkeypatch):
    """Chat-only hosted server (no /api/tags): chat works, embeddings stay keyword-only."""
    class _R:
        def __init__(self, code):
            self.status_code = code

    class _C:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            if url.endswith("/api/tags"):
                return _R(404)
            assert "Bearer" in (headers or {}).get("Authorization", "")
            return _R(200)

    monkeypatch.undo()
    monkeypatch.setattr(llm_mod.httpx, "Client", _C)
    monkeypatch.setattr(llm_mod, "_avail_cache", None)
    assert llm_mod.available() is True
    assert llm_mod.embed_available() is False
    assert llm_mod.health()["openai_compatible"] is True
    monkeypatch.setattr(llm_mod, "complete", lambda *a, **k: "Hosted answer [DUL-001 | %s]" % PAGE)
    monkeypatch.setattr(llm_mod, "embed", _boom)
    a = answer_mod.answer("Girujan stuck pipe remedy")
    assert "Hosted answer" in a.text and a.citations and not a.text.startswith(answer_mod.OFFLINE_PREFIX)
