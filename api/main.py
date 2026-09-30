"""NWIS API. Thin handlers only — all logic lives in nwis/*.

Phase 3 (this file's original scope): /wells, /nearby, /correlation, /lookahead, /dip.
Later phases mount their own routers here (see markers below) rather than
growing this file's handler bodies.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from nwis import db, llm
from nwis.geo import correlate, dip, panel
from nwis.geo.nearby import nearby_wells

logger = logging.getLogger(__name__)

app = FastAPI(title="NWIS API")


@app.on_event("startup")
async def _log_startup():
    """Log useful diagnostics on boot so operators can verify config at a glance."""
    online = llm.available()
    logger.info("NWIS API started — LLM %s", "online" if online else "offline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    online = llm.available()
    try:
        n_events = len(db.events_for())
    except Exception:  # noqa: BLE001
        n_events = -1
    return {
        "status": "ok",
        "llm": llm.health() if online else {"ollama": False},
        "deployment": {"llm": "online" if online else "offline", "db_events": n_events},
    }


@app.get("/wells")
def list_wells():
    return [w.model_dump() for w in db.all_wells()]


@app.get("/wells/{well_id}")
def get_well(well_id: str):
    w = db.get_well(well_id)
    if w is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    return w.model_dump()


@app.get("/nearby")
def get_nearby(well_id: str, radius_km: float = 3.0):
    if db.get_well(well_id) is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    return [c.model_dump() for c in nearby_wells(well_id, radius_km)]


@app.get("/correlation")
def get_correlation(well_ids: str, mode: str = "tvd"):
    ids = [w.strip() for w in well_ids.split(",") if w.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="well_ids must be a non-empty comma-separated list")
    panel_data = correlate.correlation_panel_data(ids)
    fig = panel.correlation_figure(panel_data, mode=mode)
    return {"panel_data": panel_data, "figure": json.loads(fig.to_json())}


@app.get("/lookahead")
def get_lookahead(well_id: str, md: float, lookahead_m: float = 200.0, radius_km: float = 3.0):
    if db.get_well(well_id) is None:
        raise HTTPException(status_code=404, detail=f"well {well_id!r} not found")
    return correlate.active_well_lookahead(well_id, md, lookahead_m=lookahead_m, radius_km=radius_km)


@app.get("/dip/{formation}")
def get_dip(formation: str):
    theta_deg, azimuth_deg, rmse_m, n = dip.fit_dip(formation)
    rmse_out = None if rmse_m != rmse_m else rmse_m  # NaN (< MIN_WELLS_FOR_FIT fallback) -> null
    return {"formation": formation, "theta_deg": theta_deg, "azimuth_deg": azimuth_deg, "rmse_m": rmse_out, "n": n}


# Phase 4/5/6 routers mount here:
# app.include_router(search_router)   # /search  (Phase 4 — nwis/search)
from api.risk_router import router as risk_router  # noqa: E402
from api.live_router import router as live_router  # noqa: E402

app.include_router(risk_router)  # /risk    (Phase 5 — nwis/predict)
app.include_router(live_router)  # /live/*  (Phase 6 — nwis/live)


# --------------------------------------------------------------------------- #
# Phase 7 — web UI router + built SPA (web/dist). Keep this block LAST in the file.
# --------------------------------------------------------------------------- #
from pathlib import Path as _Path  # noqa: E402

from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402
from fastapi.exception_handlers import http_exception_handler as _default_http_handler  # noqa: E402
from fastapi.responses import FileResponse as _FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles as _StaticFiles  # noqa: E402

from api.ui_router import router as ui_router  # noqa: E402

app.include_router(ui_router)  # /events /review-queue /documents /correlation/figure /map/data (Phase 7)

# The Phase 4 search router ships standalone; the UI's Memory panel needs it, so
# mount it here if nobody has at the Phase 4 hook yet.
if not any(getattr(r, "path", None) == "/search" for r in app.routes):
    from api.search_router import router as _search_router  # noqa: E402

    app.include_router(_search_router)  # /search, /answer (Phase 4)


class _ApiPrefix:
    """The SPA always calls the API under /api/* (Vite proxies it in dev); in the
    single-process demo this strips the prefix so the same build works on :8000."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == "/api" or path.startswith("/api/"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                scope["raw_path"] = scope["path"].encode()
        await self.inner(scope, receive, send)


app.add_middleware(_ApiPrefix)

_WEB_DIST = _Path(__file__).resolve().parents[1] / "web" / "dist"
if _WEB_DIST.exists() and (_WEB_DIST / "index.html").exists():
    if (_WEB_DIST / "assets").exists():
        app.mount("/assets", _StaticFiles(directory=_WEB_DIST / "assets"), name="spa-assets")

    # SPA fallback as a 404 handler rather than a catch-all route, so routers that
    # other phases mount later (e.g. /risk) are never shadowed by it.
    @app.exception_handler(_StarletteHTTPException)
    async def _spa_fallback(request, exc):
        if exc.status_code == 404 and request.method == "GET":
            rel = request.url.path.lstrip("/")
            f = (_WEB_DIST / rel).resolve() if rel else None
            if f is not None and f.is_file() and _WEB_DIST in f.parents:
                return _FileResponse(f)
            if "text/html" in request.headers.get("accept", ""):
                return _FileResponse(_WEB_DIST / "index.html")
        return await _default_http_handler(request, exc)
