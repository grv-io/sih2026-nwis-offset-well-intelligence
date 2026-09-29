"""Tests for nwis/search/. No real Ollama needed: nwis.llm.embed is monkeypatched
with a deterministic hashing-trick fake embedding (shared words -> higher cosine
similarity, same idea as a random-projection embedding) and nwis.llm.complete
with a canned answer. One integration test is skipped unless a live Ollama is
reachable (nwis.llm.health()["ollama"]).
"""
from __future__ import annotations

import hashlib
import re

import pytest

from nwis import db, schema as S
from nwis import llm as llm_mod
from nwis.search import index as index_mod
from nwis.search import retrieve as retrieve_mod
from nwis.search import answer as answer_mod
from nwis.search.retrieve import hybrid_search, structured_hits

FAKE_DIM = 64


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic hashing-trick bag-of-words embedding: shared vocabulary
    between two texts pushes their vectors toward each other, same spirit as a
    real embedding, with zero network calls."""
    out: list[list[float]] = []
    for t in texts:
        v = [0.0] * FAKE_DIM
        for word in re.findall(r"[a-z0-9]+", t.lower()):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % FAKE_DIM
            sign = 1.0 if (h // FAKE_DIM) % 2 == 0 else -1.0
            v[idx] += sign
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        out.append([x / norm for x in v])
    return out


# --------------------------------------------------------------------------- #
# Fixture: 3 wells, 6 hand-written OIL-style DDR chunks, 4 events
# --------------------------------------------------------------------------- #
PAGE_STUCK = "DUL-001_DDR_2019-03-11.txt#p1"      # W1, Girujan stuck pipe
PAGE_NOEVENT_W1 = "DUL-001_DDR_2019-03-12.txt#p1"  # W1, routine
PAGE_LOSS = "DUL-002_DDR_2019-04-05.txt#p1"        # W2, Tipam mud loss
PAGE_NOEVENT_W2 = "DUL-002_DDR_2019-04-06.txt#p1"  # W2, routine
PAGE_KICK = "MOR-001_DDR_2019-05-20.txt#p1"        # W3, Barail kick
PAGE_CEMENT = "MOR-001_DDR_2019-05-21.txt#p1"      # W3, cementing issue


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    db.reset(tmp_path / "search_test.sqlite")

    db.upsert_wells([
        S.Well(well_id="DUL-001", name="DUL-001", field="Duliajan", lat=27.30, lon=95.00, td_md_m=3000),
        S.Well(well_id="DUL-002", name="DUL-002", field="Duliajan", lat=27.31, lon=95.01, td_md_m=2800),
        S.Well(well_id="MOR-001", name="MOR-001", field="Moran", lat=27.50, lon=95.40, td_md_m=3400),
    ])

    chunks_spec = [
        ("c1", "DUL-001", PAGE_STUCK,
         "11-Mar-2019: Drilling ahead in Girujan Clay at 2450m. Pipe got stuck while pulling "
         "out of hole; worked free after spotting pill and jarring up. NPT 6 hrs."),
        ("c2", "DUL-001", PAGE_NOEVENT_W1,
         "12-Mar-2019: Drilling ahead 2500-2550m in Girujan Clay, no problems. Circulated "
         "bottoms up, mud checks normal."),
        ("c3", "DUL-002", PAGE_LOSS,
         "05-Apr-2019: While drilling Tipam Sandstone at 1800m, observed partial mud loss into "
         "formation. Pumped LCM pill, losses reduced. NPT 3 hrs."),
        ("c4", "DUL-002", PAGE_NOEVENT_W2,
         "06-Apr-2019: Tripping in hole, routine. No events."),
        ("c5", "MOR-001", PAGE_KICK,
         "20-May-2019: Kick while drilling Barail Group at 3200m; shut in well, increased mud "
         "weight from 11.5 to 12.8 ppg. Well controlled per procedure."),
        ("c6", "MOR-001", PAGE_CEMENT,
         "21-May-2019: Cementing 9-5/8 casing at 2600m; float valve failure noted, top job "
         "performed."),
    ]
    db.add_chunks([
        db.ChunkRow(chunk_id=cid, document_id=f"doc-{cid}", well_id=well_id, page_ref=page_ref, text=text)
        for cid, well_id, page_ref, text in chunks_spec
    ])

    db.upsert_events([
        S.DrillingEvent(
            event_id="e1", well_id="DUL-001", source_document_id="doc-c1", source_page_ref=PAGE_STUCK,
            event_type=S.EventType.stuck_pipe, depth_md_m=2450, formation="Girujan",
            hours_lost_npt=6, cause="pipe stuck pulling out of hole", remedy="spotted pill, jarred up",
            free_text="Pipe got stuck while pulling out of hole", extraction_confidence=0.9,
        ),
        S.DrillingEvent(
            event_id="e2", well_id="DUL-002", source_document_id="doc-c3", source_page_ref=PAGE_LOSS,
            event_type=S.EventType.mud_loss, depth_md_m=1800, formation="Tipam",
            hours_lost_npt=3, cause="partial mud loss into formation", remedy="pumped LCM pill",
            free_text="observed partial mud loss into formation", extraction_confidence=0.9,
        ),
        S.DrillingEvent(
            event_id="e3", well_id="MOR-001", source_document_id="doc-c5", source_page_ref=PAGE_KICK,
            event_type=S.EventType.kick, depth_md_m=3200, formation="Barail",
            mud_weight_ppg_at_event=12.8, cause="kick", remedy="shut in, raised mud weight to 12.8 ppg",
            free_text="Kick while drilling; shut in well", extraction_confidence=0.9,
        ),
        S.DrillingEvent(
            event_id="e4", well_id="MOR-001", source_document_id="doc-c6", source_page_ref=PAGE_CEMENT,
            event_type=S.EventType.cementing_issue, depth_md_m=2600, formation=None,
            cause="float valve failure", remedy="top job performed",
            free_text="Cementing 9-5/8 casing; float valve failure", extraction_confidence=0.8,
        ),
    ])

    monkeypatch.setattr(llm_mod, "embed", fake_embed)
    monkeypatch.setattr(llm_mod, "available", lambda: True)
    monkeypatch.setattr(llm_mod, "embed_available", lambda: True)
    monkeypatch.setattr(index_mod, "INDEX_PATH", tmp_path / "chunk_index.npz")
    monkeypatch.setattr(index_mod, "IDS_PATH", tmp_path / "chunk_index_ids.json")
    index_mod.build(progress=False)

    return tmp_path


# --------------------------------------------------------------------------- #
# hybrid_search
# --------------------------------------------------------------------------- #
def test_hybrid_search_ranks_stuck_pipe_girujan_first(fixture_db):
    hits = hybrid_search("stuck pipe Girujan", k=8)
    assert hits, "expected at least one hit"
    assert hits[0].well_id == "DUL-001"
    assert hits[0].page_ref == PAGE_STUCK
    assert hits[0].why  # RRF fusion should have recorded rank info


def test_hybrid_search_filters_by_well_id(fixture_db):
    hits = hybrid_search("drilling", k=8, filters={"well_ids": ["DUL-002"]})
    assert hits
    assert all(h.well_id == "DUL-002" for h in hits)


def test_hybrid_search_filters_by_formation_via_events(fixture_db):
    # Only c3 (DUL-002) has an event whose formation is Tipam; c4 (also DUL-002,
    # no event) must be excluded even though it matches the well.
    hits = hybrid_search("drilling", k=8, filters={"formation": "Tipam"})
    assert hits
    assert all(h.page_ref == PAGE_LOSS for h in hits)


def test_hybrid_search_filters_by_event_type(fixture_db):
    hits = hybrid_search("drilling", k=8, filters={"event_type": "kick"})
    assert hits
    assert all(h.page_ref == PAGE_KICK for h in hits)


def test_hybrid_search_radius_from_well_excludes_far_well(fixture_db):
    # DUL-002 is ~1.5km from DUL-001; MOR-001 is ~40km away.
    hits = hybrid_search("drilling", k=8, filters={"radius_from_well": "DUL-001", "radius_km": 5})
    assert hits
    assert all(h.well_id in {"DUL-001", "DUL-002"} for h in hits)
    assert not any(h.well_id == "MOR-001" for h in hits)


def test_hybrid_search_rrf_fuses_both_rankers(fixture_db):
    hits = hybrid_search("stuck pipe Girujan", k=8)
    top = hits[0]
    assert "fts" in top.why and "cosine" in top.why


# --------------------------------------------------------------------------- #
# structured_hits
# --------------------------------------------------------------------------- #
def test_structured_hits_infers_event_type_from_synonym(fixture_db):
    rows = structured_hits("losses")
    assert rows
    assert all(r.event_type == "mud_loss" for r in rows)
    assert rows[0].well_id == "DUL-002"


def test_structured_hits_stuck_synonym(fixture_db):
    rows = structured_hits("stuck pipe remedy")
    assert rows
    assert all(r.event_type == "stuck_pipe" for r in rows)


def test_structured_hits_explicit_filters_override_query(fixture_db):
    rows = structured_hits("anything", filters={"event_type": "cementing_issue"})
    assert len(rows) == 1
    assert rows[0].event_id == "e4"


# --------------------------------------------------------------------------- #
# answer() -- hallucination guard + refusal
# --------------------------------------------------------------------------- #
def test_answer_strips_fabricated_citation_tag(fixture_db, monkeypatch):
    canned = (
        f"Stuck pipe in Girujan at 2450m on DUL-001; spotted a pill and jarred free, 6 hrs NPT "
        f"[DUL-001 | {PAGE_STUCK}]. A rig also caught fire and sank [FAKE-999 | nope.pdf#p1]."
    )
    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: canned)

    result = answer_mod.answer("stuck pipe Girujan remedy")

    assert not result.refused
    assert "FAKE-999" not in result.text
    assert f"[DUL-001 | {PAGE_STUCK}]" in result.text
    assert not result.degraded
    assert len(result.citations) == 1
    assert result.citations[0].well_id == "DUL-001"
    assert result.citations[0].page_ref == PAGE_STUCK


def test_answer_degrades_when_all_citations_fake(fixture_db, monkeypatch):
    canned = "Something happened [FAKE-1 | a.pdf#p1] and also [FAKE-2 | b.pdf#p2]."
    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: canned)

    result = answer_mod.answer("stuck pipe Girujan remedy")

    assert not result.refused
    assert result.degraded
    # fake tags are stripped; the retrieved evidence is attached explicitly instead (26 Sep)
    assert "FAKE-1" not in result.text and "FAKE-2" not in result.text
    assert result.citations and all(c.well_id != "FAKE-1" for c in result.citations)
    assert "Sources (auto-attached" in result.text


def test_scope_guard_greeting_and_off_topic(fixture_db, monkeypatch):
    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: "should not be called")
    for q in ["hi", "Hello!", "namaste", "thanks", "who are you?"]:
        a = answer_mod.answer(q)
        assert a.guard == "greeting" and not a.refused and a.suggestions and "archive" in a.text
    a = answer_mod.answer("what is the capital of France")
    assert a.guard == "off_topic" and "drilling archive" in a.text
    # a real archive question is never guarded, even without a well id
    assert answer_mod._query_kind("stuck pipe Girujan remedy") is None
    assert answer_mod._query_kind("DUL-005 ke paas kya hua") is None


def test_scope_guard_lang_orders_the_fixed_line(fixture_db, monkeypatch):
    """lang only reorders the bilingual guard line (UI EN|हि toggle); absent == historical default."""
    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: "should not be called")
    default = answer_mod.answer("hi")
    assert answer_mod.answer("hi", lang="en").text == default.text  # "en" == unchanged default
    assert default.text.startswith("Hello.")
    hi = answer_mod.answer("hi", lang="hi")
    first, second = hi.text.split("\n", 1)
    assert first.startswith("नमस्ते") and second.startswith("Hello.")
    assert "archive" in hi.text and hi.guard == "greeting" and not hi.refused
    # the Hindi example prompt leads the examples, and is itself an archive question
    assert first.index(answer_mod.HINDI_EXAMPLE) < first.index(answer_mod.EXAMPLE_QUESTIONS[0])
    assert answer_mod._query_kind(answer_mod.HINDI_EXAMPLE) is None

    off = answer_mod.answer("what is the capital of France", lang="hi")
    assert off.guard == "off_topic" and off.text.startswith("मैं सिर्फ़") and "drilling archive" in off.text
    # suggestions (English archive questions) do not depend on the UI language
    assert off.suggestions == default.suggestions == answer_mod.EXAMPLE_QUESTIONS
    # a real archive question is never affected by lang
    monkeypatch.setattr(llm_mod, "complete",
                        lambda system, user, **kw: f"Freed by jarring [DUL-001 | {PAGE_STUCK}].")
    real = answer_mod.answer("stuck pipe Girujan remedy", lang="hi")
    assert real.guard is None and f"[DUL-001 | {PAGE_STUCK}]" in real.text


def test_answer_endpoint_threads_lang(fixture_db, monkeypatch):
    """POST /answer accepts optional lang and routes the guard line by it (api/search_router.py)."""
    from fastapi.testclient import TestClient
    from api import search_router

    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: "should not be called")
    client = TestClient(search_router.app)

    r = client.post("/answer", json={"query": "namaste", "lang": "hi"})  # use_cache defaults to True
    assert r.status_code == 200
    body = r.json()
    assert body["guard"] == "greeting" and body["text"].startswith("नमस्ते")

    r = client.post("/answer", json={"query": "hello", "use_cache": False, "lang": "en"})
    assert r.status_code == 200 and r.json()["text"].startswith("Hello.")

    # lang absent: exactly the pre-toggle behaviour (non-cache path shown; cache path is untouched)
    r = client.post("/answer", json={"query": "hello", "use_cache": False})
    assert r.status_code == 200 and r.json()["text"] == answer_mod._guard_answer("greeting").text

    # the non-cache path threads lang into answer(); archive questions are unaffected by it
    seen = {}
    real_answer = answer_mod.answer

    def spy(query, filters=None, lang=None):
        seen["lang"] = lang
        return real_answer(query, filters=filters, lang=lang)

    monkeypatch.setattr(search_router, "answer_mod", spy)
    r = client.post("/answer", json={"query": "who are you?", "use_cache": False, "lang": "hi"})
    assert r.status_code == 200 and r.json()["guard"] == "greeting"  # answered before answer()
    monkeypatch.setattr(llm_mod, "complete",
                        lambda system, user, **kw: f"Freed by jarring [DUL-001 | {PAGE_STUCK}].")
    r = client.post("/answer", json={"query": "stuck pipe Girujan remedy", "use_cache": False, "lang": "hi"})
    assert r.status_code == 200 and seen["lang"] == "hi" and r.json()["guard"] is None

    # anything but en|hi is rejected by validation
    assert client.post("/answer", json={"query": "hello", "lang": "fr"}).status_code == 422


def test_friendly_incident_line_format():
    raw = "Direct answer.\nDUL-008, 626 m MD, Girujan, 85.3 h NPT - freed on jarring [DUL-008 | DUL-008_WCR.pdf#p3]"
    out = answer_mod._friendly(raw)
    assert out.splitlines()[1] == "DUL-008 · 626 m MD · Girujan · 85.3 h NPT — freed on jarring [DUL-008 | DUL-008_WCR.pdf#p3]"


def test_answer_refuses_when_no_evidence(fixture_db, monkeypatch):
    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: "should not be called")

    result = answer_mod.answer("stuck pipe Girujan remedy", filters={"well_ids": ["NO-SUCH-WELL"]})

    assert result.refused
    assert result.citations == []


def test_answer_refuses_on_explicit_llm_refusal_marker(fixture_db, monkeypatch):
    monkeypatch.setattr(llm_mod, "complete", lambda system, user, **kw: "INSUFFICIENT_EVIDENCE")

    result = answer_mod.answer("stuck pipe Girujan remedy")

    # Evidence exists, so the marker no longer yields a bare refusal: the matching
    # incidents are shown as a cited structured fallback instead (26 Sep).
    assert not result.refused and result.degraded
    assert result.text.startswith("No narrative answer")
    assert result.citations and result.structured


# --------------------------------------------------------------------------- #
# Integration (real Ollama) -- skipped unless reachable
# --------------------------------------------------------------------------- #
def test_live_embed_and_answer_on_fixture(fixture_db, monkeypatch):
    health = llm_mod.health()
    if not health.get("ollama"):
        pytest.skip("Ollama not reachable")

    # fixture_db monkeypatched embed/complete + the index cache paths onto the
    # shared `monkeypatch` fixture instance; undo that here so this test hits
    # the real Ollama endpoint instead of the deterministic fake.
    monkeypatch.undo()

    vecs = llm_mod.embed(["stuck pipe in Girujan formation"])
    assert len(vecs) == 1 and len(vecs[0]) > 0

    result = answer_mod.answer("Girujan stuck pipe remedy")
    assert isinstance(result.text, str) and result.text
