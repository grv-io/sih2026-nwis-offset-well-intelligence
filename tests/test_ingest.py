"""Tests for nwis/ingest/. Most of these need no Ollama; one integration test is
skipped automatically when Ollama isn't reachable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nwis import db, schema as S
from nwis.ingest.chunk import Chunk, chunk_page
from nwis.ingest.extract_text import PageText
from nwis.ingest.normalise import canonical_formation, md_to_tvd
from nwis.schema import EventType, ExtractedEvents, LLMEvent

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# chunk.py
# --------------------------------------------------------------------------- #
def test_chunk_page_on_sample_ddr_text():
    text = (FIXTURES / "DUL-011_DDR_2019-03-11.txt").read_text(encoding="utf-8")
    page = PageText(page_no=1, text=text, method="txt")
    chunks = chunk_page(page, "DUL-011_DDR_2019-03-11.txt")

    assert len(chunks) >= 1
    for c in chunks:
        assert c.page_ref == "DUL-011_DDR_2019-03-11.txt#p1"
        assert len(c.text) <= 1200
    assert any("stuck" in c.text.lower() for c in chunks)


def test_chunk_page_splits_on_date_headers_when_multiple():
    text = (
        "11-Mar-2019: Drilling ahead 2400-2450m, no problems.\n"
        "12-Mar-2019: Drilling ahead 2450-2500m, no problems.\n"
        "13-Mar-2019: Drilling ahead 2500-2550m, no problems.\n"
    )
    page = PageText(page_no=3, text=text, method="pdfplumber")
    chunks = chunk_page(page, "sample.pdf")

    assert len(chunks) == 3
    assert all(c.page_ref == "sample.pdf#p3" for c in chunks)
    assert chunks[0].text.startswith("11-Mar-2019")
    assert chunks[2].text.startswith("13-Mar-2019")


def test_chunk_page_empty_text_returns_nothing():
    page = PageText(page_no=1, text="   \n\n  ", method="txt")
    assert chunk_page(page, "empty.txt") == []


# --------------------------------------------------------------------------- #
# extract_events.py — hallucination guard (no LLM call: nwis.llm.extract is monkeypatched)
# --------------------------------------------------------------------------- #
def test_quote_verification_drops_fabricated_event(monkeypatch):
    from nwis.ingest import extract_events as EE

    chunk = Chunk(text="Drilling ahead 2500-2550m, no problems. NPT nil.", page_ref="f.txt#p1")

    real = LLMEvent(event_type=EventType.npt_other, quote="no problems", confidence=0.8)
    fabricated = LLMEvent(event_type=EventType.kick, quote="massive blowout at surface, well out of control",
                           confidence=0.95)

    def fake_extract(model, system, user):
        assert model is ExtractedEvents
        return ExtractedEvents(events=[real, fabricated])

    monkeypatch.setattr(EE.llm_mod, "extract", fake_extract)

    kept = EE.extract_from_chunk(chunk, {"well_id": "W1"})

    assert len(kept) == 1
    assert kept[0].event_type == EventType.npt_other


# --------------------------------------------------------------------------- #
# normalise.py
# --------------------------------------------------------------------------- #
def test_canonical_formation_aliases():
    assert canonical_formation("Gurjan") == "Girujan"
    assert canonical_formation("TIPAM SST") == "Tipam"
    assert canonical_formation("Marcellus") is None


def test_md_to_tvd_vertical_survey_equals_md(tmp_path):
    db.reset(tmp_path / "t.sqlite")
    db.add_surveys([
        S.SurveyStation(well_id="W1", md_m=0, inc_deg=0, azi_deg=0),
        S.SurveyStation(well_id="W1", md_m=500, inc_deg=0, azi_deg=0),
        S.SurveyStation(well_id="W1", md_m=1000, inc_deg=0, azi_deg=0),
        S.SurveyStation(well_id="W1", md_m=2000, inc_deg=0, azi_deg=0),
    ])
    assert md_to_tvd("W1", 1500) == pytest.approx(1500, abs=0.01)
    assert md_to_tvd("W1", 2000) == pytest.approx(2000, abs=0.01)


def test_md_to_tvd_no_surveys_falls_back_to_md(tmp_path):
    db.reset(tmp_path / "t2.sqlite")
    assert md_to_tvd("NO-SUCH-WELL", 1234.5) == 1234.5


# --------------------------------------------------------------------------- #
# Integration: real Ollama call. Skipped automatically if Ollama isn't running.
# --------------------------------------------------------------------------- #
def _ollama_up() -> bool:
    from nwis.llm import health
    return bool(health().get("ollama", False))


@pytest.mark.skipif(not _ollama_up(), reason="Ollama not reachable")
def test_extract_mud_loss_from_real_llm():
    from nwis.ingest.extract_events import extract_from_chunk

    text = (
        "12-Mar-2019: Drilling ahead 2500-2545m in Tipam sst, mud losses observed, "
        "pit level dropped 10 bbl in 20 min with no gain. Pumped 30 bbl LCM pill, "
        "regained full returns after 1 hr."
    )
    chunk = Chunk(text=text, page_ref="fixture.txt#p1")
    events = extract_from_chunk(chunk, {"well_id": "TEST-1"})

    assert sum(1 for e in events if e.event_type == EventType.mud_loss) >= 1
