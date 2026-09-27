from nwis import schema as S


def test_event_taxonomy_closed():
    assert "mud_loss" in [e.value for e in S.EventType]
    assert len(S.EventType) == 12


def test_formation_alias_normalises():
    e = S.DrillingEvent(
        event_id="x", well_id="w", source_document_id="d", source_page_ref="d#p1",
        event_type=S.EventType.stuck_pipe, formation="Gurjan",
    )
    assert e.formation == "Girujan"


def test_unknown_formation_rejected():
    import pytest
    with pytest.raises(ValueError):
        S.FormationTop(well_id="w", formation="Marcellus", top_md_m=1, top_tvd_m=1)


def test_db_roundtrip(tmp_path):
    from nwis import db
    db.reset(tmp_path / "t.sqlite")
    db.upsert_wells([S.Well(well_id="W1", name="W1", field="Duliajan", lat=27.4, lon=95.3, td_md_m=3000)])
    db.upsert_events([S.DrillingEvent(
        event_id="e1", well_id="W1", source_document_id="d1", source_page_ref="d1#p1",
        event_type=S.EventType.mud_loss, depth_md_m=2100, formation="Tipam", extraction_confidence=0.4,
    )])
    assert len(db.all_wells()) == 1
    assert db.events_for(["W1"])[0].formation == "Tipam"
    assert len(db.review_queue()) == 1
