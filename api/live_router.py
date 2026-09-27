"""Phase 6 (live replay + alerts) API surface.

Mounted in api/main.py at the "# Phase 6" hook:

    from api.live_router import router as live_router
    app.include_router(live_router)  # /live/*  (Phase 6 — nwis/live)

Single active ReplaySession per process (this is a demo rig-floor console, not a
multi-tenant service) -- matches the "one replay well at a time" shape of the demo
script in docs/IMPLEMENTATION_PLAN.md Phase 7.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from nwis.live.replay import ReplaySession

router = APIRouter(prefix="/live", tags=["live"])

_session: Optional[ReplaySession] = None


class StartRequest(BaseModel):
    well_id: str
    speed: float = 20.0


class SeekRequest(BaseModel):
    md: float


def _require_session() -> ReplaySession:
    if _session is None:
        raise HTTPException(status_code=409, detail="no active replay session; call POST /live/start first")
    return _session


# NOTE: every handler below is `async def`, not plain `def`. FastAPI runs sync `def`
# endpoints in a worker thread (starlette run_in_threadpool), which has no running
# asyncio event loop -- ReplaySession.start()'s asyncio.create_task() would raise
# "no running event loop" there. `async def` keeps these on the server's own loop,
# which is also the loop the replay task runs on.
@router.post("/start")
async def start(req: StartRequest) -> dict:
    global _session
    if _session is not None:
        _session.stop()
    _session = ReplaySession(well_id=req.well_id, speed=req.speed)
    _session.start()
    return _session.state()


@router.post("/pause")
async def pause() -> dict:
    _require_session().pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume() -> dict:
    _require_session().resume()
    return {"status": "resumed"}


@router.post("/stop")
async def stop() -> dict:
    _require_session().stop()
    return {"status": "stopped"}


@router.post("/seek")
async def seek(req: SeekRequest) -> dict:
    sess = _require_session()
    sess.seek(req.md)
    return sess.state()


@router.get("/state")
async def state() -> dict:
    return _require_session().state()


@router.get("/alerts")
async def alerts(include_acked: bool = False) -> list[dict]:
    sess = _require_session()
    return [a.model_dump() for a in sess.alert_engine.open_alerts(include_acked=include_acked)]


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str) -> dict:
    sess = _require_session()
    if not sess.alert_engine.ack(alert_id):
        raise HTTPException(status_code=404, detail=f"alert {alert_id!r} not found among open alerts")
    return {"status": "acked", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/shelve")
async def shelve_alert(alert_id: str) -> dict:
    sess = _require_session()
    if not sess.alert_engine.shelve(alert_id):
        raise HTTPException(status_code=404, detail=f"alert {alert_id!r} not found among open alerts")
    return {"status": "shelved", "alert_id": alert_id}


@router.get("/signals")
async def signals() -> dict:
    """Raw anomaly z-scores + rule flags for the current window, for debugging."""
    import pandas as pd
    from nwis.live import anomaly as anom

    sess = _require_session()
    window_df = pd.DataFrame(sess.window) if sess.window else pd.DataFrame()
    return anom.debug_signals(window_df)
