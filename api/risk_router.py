"""Phase 5 (predictive layer) API surface.

Mounted in api/main.py at the "# Phase 5" hook:

    from api.risk_router import router as risk_router
    app.include_router(risk_router)   # /risk  (Phase 5 — nwis/predict)
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from nwis import db
from nwis.config import settings
from nwis.predict import risk_ahead

router = APIRouter(tags=["risk"])

METRICS_PATH = settings.models_dir / "predict_metrics.json"
SUMMARY_PATH = settings.models_dir / "risk_summary.json"


@router.get("/risk")
def get_risk(well_id: str, md: float, radius_km: float = Query(3.0, ge=0.0)) -> list[dict]:
    if db.get_well(well_id) is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    intervals = risk_ahead.score(well_id, md, radius_km=radius_km)
    return [i.model_dump() for i in intervals]


@router.get("/risk/metrics")
def get_risk_metrics() -> dict:
    if not METRICS_PATH.exists():
        raise HTTPException(status_code=404, detail="predict_metrics.json not found; run `python -m nwis.predict.train` first")
    return json.loads(METRICS_PATH.read_text())


@router.get("/risk/summary")
def get_risk_summary(formation: Optional[str] = None) -> dict:
    if not SUMMARY_PATH.exists():
        raise HTTPException(status_code=404, detail="risk_summary.json not found; run `python -m nwis.predict.train` first")
    data = json.loads(SUMMARY_PATH.read_text())
    if formation is None:
        return data
    if formation not in data:
        raise HTTPException(status_code=404, detail=f"no risk_summary bins for formation {formation!r}")
    return {formation: data[formation]}
