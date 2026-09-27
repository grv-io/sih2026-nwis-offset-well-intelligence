"""SQLite persistence via SQLModel. Thin repository functions; no business logic here.

Tables mirror nwis.schema. Swap for PostGIS/pgvector later by re-implementing this module only.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine, select

from nwis import schema as S
from nwis.config import settings


class WellRow(SQLModel, table=True):
    __tablename__ = "wells"
    well_id: str = Field(primary_key=True)
    name: str
    field: str
    lat: float
    lon: float
    kb_elev_m: float = 0.0
    spud_date: Optional[date] = None
    td_md_m: float
    trajectory_type: str = "vertical"
    status: str = "completed"
    basin: str = "Upper Assam"


class TopRow(SQLModel, table=True):
    __tablename__ = "formation_tops"
    id: Optional[int] = Field(default=None, primary_key=True)
    well_id: str = Field(index=True)
    formation: str
    top_md_m: float
    top_tvd_m: float
    base_md_m: Optional[float] = None
    base_tvd_m: Optional[float] = None


class SurveyRow(SQLModel, table=True):
    __tablename__ = "surveys"
    id: Optional[int] = Field(default=None, primary_key=True)
    well_id: str = Field(index=True)
    md_m: float
    inc_deg: float
    azi_deg: float


class DocRow(SQLModel, table=True):
    __tablename__ = "documents"
    document_id: str = Field(primary_key=True)
    well_id: str = Field(index=True)
    doc_type: str
    path: str
    report_date: Optional[date] = None
    n_pages: int = 1
    is_scanned: bool = False


class EventRow(SQLModel, table=True):
    __tablename__ = "events"
    event_id: str = Field(primary_key=True)
    well_id: str = Field(index=True)
    source_document_id: str
    source_page_ref: str
    report_date: Optional[date] = None
    depth_md_m: Optional[float] = None
    depth_tvd_m: Optional[float] = None
    interval_top_md_m: Optional[float] = None
    interval_base_md_m: Optional[float] = None
    formation: Optional[str] = Field(default=None, index=True)
    formation_offset_from_top_m: Optional[float] = None
    event_type: str = Field(index=True)
    severity: str = "medium"
    hours_lost_npt: Optional[float] = None
    cause: Optional[str] = None
    remedy: Optional[str] = None
    mud_weight_ppg_at_event: Optional[float] = None
    ecd_ppg_at_event: Optional[float] = None
    free_text: str = ""
    extraction_confidence: float = 0.5
    extraction_method: str = "ocr_llm_local"
    reviewed_by_human: bool = False


class ChunkRow(SQLModel, table=True):
    """Text chunk for search; embedding stored as JSON list (small corpus)."""
    __tablename__ = "chunks"
    chunk_id: str = Field(primary_key=True)
    document_id: str = Field(index=True)
    well_id: str = Field(index=True)
    page_ref: str
    text: str
    embedding_json: Optional[str] = None


class ReviewRow(SQLModel, table=True):
    __tablename__ = "review_queue"
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: str
    reason: str
    resolved: bool = False


# --------------------------------------------------------------------------- #
_engine = None


def engine(path: Optional[Path] = None):
    global _engine
    if _engine is None or path is not None:
        p = Path(path) if path else settings.db_path
        p.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{p}", echo=False)
        SQLModel.metadata.create_all(_engine)
        with _engine.connect() as c:
            c.execute(text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text)"
            ))
            c.commit()
    return _engine


def session() -> Session:
    return Session(engine())


# ---- writers ----------------------------------------------------------------
def upsert_wells(wells: Iterable[S.Well]) -> int:
    n = 0
    with session() as s:
        for w in wells:
            s.merge(WellRow(**w.model_dump()))
            n += 1
        s.commit()
    return n


def add_tops(tops: Iterable[S.FormationTop]) -> int:
    n = 0
    with session() as s:
        for t in tops:
            s.add(TopRow(**t.model_dump()))
            n += 1
        s.commit()
    return n


def add_surveys(stations: Iterable[S.SurveyStation]) -> int:
    n = 0
    with session() as s:
        for st in stations:
            s.add(SurveyRow(**st.model_dump()))
            n += 1
        s.commit()
    return n


def upsert_documents(docs: Iterable[S.SourceDocument]) -> int:
    n = 0
    with session() as s:
        for d in docs:
            s.merge(DocRow(**d.model_dump()))
            n += 1
        s.commit()
    return n


def upsert_events(events: Iterable[S.DrillingEvent], review_threshold: float = 0.6) -> int:
    n = 0
    with session() as s:
        for e in events:
            d = e.model_dump()
            d["event_type"] = e.event_type.value
            d["severity"] = e.severity.value
            d["extraction_method"] = e.extraction_method.value
            s.merge(EventRow(**d))
            if e.extraction_confidence < review_threshold and not e.reviewed_by_human:
                s.add(ReviewRow(event_id=e.event_id, reason=f"confidence {e.extraction_confidence:.2f}"))
            n += 1
        s.commit()
    return n


def add_chunks(chunks: Iterable[ChunkRow]) -> int:
    n = 0
    with session() as s:
        for c in chunks:
            s.merge(c)
            s.exec(text("INSERT OR REPLACE INTO chunks_fts(chunk_id, text) VALUES (:i, :t)").bindparams(i=c.chunk_id, t=c.text))
            n += 1
        s.commit()
    return n


# ---- readers ----------------------------------------------------------------
def all_wells() -> list[WellRow]:
    with session() as s:
        return list(s.exec(select(WellRow)).all())


def get_well(well_id: str) -> Optional[WellRow]:
    with session() as s:
        return s.get(WellRow, well_id)


def tops_for(well_id: str) -> list[TopRow]:
    with session() as s:
        return list(s.exec(select(TopRow).where(TopRow.well_id == well_id).order_by(TopRow.top_md_m)).all())


def surveys_for(well_id: str) -> list[SurveyRow]:
    with session() as s:
        return list(s.exec(select(SurveyRow).where(SurveyRow.well_id == well_id).order_by(SurveyRow.md_m)).all())


def events_for(well_ids: Optional[list[str]] = None, event_type: Optional[str] = None,
               formation: Optional[str] = None) -> list[EventRow]:
    with session() as s:
        q = select(EventRow)
        if well_ids:
            q = q.where(EventRow.well_id.in_(well_ids))  # type: ignore[attr-defined]
        if event_type:
            q = q.where(EventRow.event_type == event_type)
        if formation:
            q = q.where(EventRow.formation == formation)
        return list(s.exec(q).all())


def documents_for(well_id: Optional[str] = None) -> list[DocRow]:
    with session() as s:
        q = select(DocRow)
        if well_id:
            q = q.where(DocRow.well_id == well_id)
        return list(s.exec(q).all())


def all_chunks() -> list[ChunkRow]:
    with session() as s:
        return list(s.exec(select(ChunkRow)).all())


def fts_search(query: str, limit: int = 20) -> list[str]:
    """Keyword search over chunks; returns chunk_ids ranked by bm25."""
    with session() as s:
        rows = s.exec(text(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH :q ORDER BY bm25(chunks_fts) LIMIT :n"
        ).bindparams(q=query, n=limit)).all()
        return [r[0] for r in rows]


def review_queue() -> list[ReviewRow]:
    with session() as s:
        return list(s.exec(select(ReviewRow).where(ReviewRow.resolved == False)).all())  # noqa: E712


def reset(path: Optional[Path] = None) -> None:
    """Drop everything. Used by tests and the synthetic loader."""
    global _engine
    p = Path(path) if path else settings.db_path
    _engine = None
    if p.exists():
        p.unlink()
    engine(p)


def embedding_of(row: ChunkRow) -> Optional[list[float]]:
    return json.loads(row.embedding_json) if row.embedding_json else None
