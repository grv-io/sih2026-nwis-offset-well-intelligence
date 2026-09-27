"""Phase 4 (knowledge search) API surface.

If `api/main.py` exists with a `# Phase 4` mount hook, mount this router there:

    from api.search_router import router as search_router
    app.include_router(search_router)  # Phase 4

Until then this module is self-contained and can be run standalone for manual
testing:  uvicorn api.search_router:app --reload
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from nwis.search.answer import _guard_answer, _query_kind, answer as answer_mod
from nwis.search.demo_cache import answer as cached_answer
from nwis.search.retrieve import hybrid_search

router = APIRouter(tags=["search"])


class AnswerRequest(BaseModel):
    query: str
    filters: Optional[dict] = None
    use_cache: bool = True
    # UI language: decides whether the fixed greeting/off-topic line leads in English or Hindi.
    # Absent = the historical behaviour (English first). Archive answers are always English.
    lang: Optional[Literal["en", "hi"]] = None


@router.get("/search")
def search(
    q: str = Query(..., description="Free-text query"),
    k: int = Query(8, ge=1, le=50),
    well_ids: Optional[str] = Query(None, description="Comma-separated well ids"),
    formation: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    radius_from_well: Optional[str] = Query(None),
    radius_km: Optional[float] = Query(None),
) -> list[dict]:
    filters = {
        "well_ids": [w.strip() for w in well_ids.split(",") if w.strip()] if well_ids else None,
        "formation": formation,
        "event_type": event_type,
        "radius_from_well": radius_from_well,
        "radius_km": radius_km,
    }
    hits = hybrid_search(q, k=k, filters=filters)
    return [asdict(h) for h in hits]


@router.post("/answer")
def answer_endpoint(req: AnswerRequest) -> dict:
    if req.lang is not None:
        # The guard line is language-dependent, so answer it here, ahead of the (language-blind)
        # demo cache; it never touches retrieval or the LLM.
        kind = _query_kind(req.query)
        if kind is not None:
            return asdict(_guard_answer(kind, req.lang))
    if req.use_cache:
        result = cached_answer(req.query, filters=req.filters, use_cache=True)
    else:
        result = answer_mod(req.query, filters=req.filters, lang=req.lang)
    return asdict(result)


# Standalone runnable app, used only until api/main.py exists / for manual testing.
try:
    from fastapi import FastAPI

    app = FastAPI(title="NWIS search (standalone)")
    app.include_router(router)
except Exception:  # noqa: BLE001
    app = None
