from datetime import date

from nwis import db
from nwis import schema as S
from nwis.ingest.link import link_incidents


def _ev(eid, well, doc, ref, et, depth, conf=0.9, method=S.ExtractionMethod.ocr_llm_local, d=date(2019, 3, 11)):
    return S.DrillingEvent(event_id=eid, well_id=well, source_document_id=doc, source_page_ref=ref,
                           event_type=et, depth_md_m=depth, extraction_confidence=conf,
                           extraction_method=method, report_date=d, free_text="x")


def test_ddr_wcr_mentions_merge_into_one_incident(tmp_path):
    db.reset(tmp_path / "t.sqlite")
    db.upsert_wells([S.Well(well_id="W", name="W", field="Duliajan", lat=27.4, lon=95.3, td_md_m=3000)])
    db.upsert_documents([
        S.SourceDocument(document_id="D1", well_id="W", doc_type="DDR", path="D1.pdf"),
        S.SourceDocument(document_id="D2", well_id="W", doc_type="WCR", path="D2.pdf"),
    ])
    db.upsert_events([
        _ev("a", "W", "D1", "D1.pdf#p1", S.EventType.stuck_pipe, 768.0, conf=0.8),
        _ev("b", "W", "D2", "D2.pdf#p3", S.EventType.stuck_pipe, 770.0, conf=0.95),   # WCR copy, higher conf
        _ev("c", "W", "D1", "D1.pdf#p1", S.EventType.stuck_pipe, 1500.0),             # different incident
        _ev("d", "W", "D1", "D1.pdf#p1", S.EventType.mud_loss, 769.0),                # different type
        _ev("t", "W", "D1", "D1.pdf#p1", S.EventType.stuck_pipe, 768.0, conf=1.0,
            method=S.ExtractionMethod.synthetic_truth),                                # protected
    ])
    r = link_incidents()
    assert r == {"mentions": 4, "merged": 1, "incidents": 3, "dry_run": False}
    left = {e.event_id: e for e in db.events_for()}
    assert "b" not in left and {"a", "c", "d", "t"} <= set(left)      # DDR kept over WCR despite lower conf
    assert "also reported in: D2.pdf#p3" in left["a"].free_text        # provenance survives
    assert link_incidents() == {"mentions": 3, "merged": 0, "incidents": 3, "dry_run": False}  # idempotent


def test_mixed_dated_and_undated_events_still_link(tmp_path):
    """Regression (review 26 Sep): a str fallback in the sort key raised TypeError when one
    event in a (well, type) group had no report_date."""
    db.reset(tmp_path / "t.sqlite")
    db.upsert_wells([S.Well(well_id="W", name="W", field="Duliajan", lat=27.4, lon=95.3, td_md_m=3000)])
    db.upsert_documents([
        S.SourceDocument(document_id="D1", well_id="W", doc_type="DDR", path="D1.pdf"),
        S.SourceDocument(document_id="D2", well_id="W", doc_type="WCR", path="D2.pdf"),
    ])
    db.upsert_events([
        _ev("a", "W", "D1", "D1.pdf#p1", S.EventType.mud_loss, 900.0),
        _ev("b", "W", "D2", "D2.pdf#p2", S.EventType.mud_loss, 905.0, d=None),
    ])
    assert link_incidents()["merged"] == 1
