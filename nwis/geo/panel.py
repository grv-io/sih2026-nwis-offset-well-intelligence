"""Plotly figure factories for the correlation panel and the offset map.

Kept dependency-light on purpose: these take plain dicts/lists (from
correlate.py / nearby.py) rather than db handles, so app/ (Streamlit,
Phase 7) and api/main.py can both call them without extra coupling.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from nwis.geo.distance import circle_polygon
from nwis.schema import FORMATIONS

_QUALITATIVE = [
    "#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2", "#eeca3b",
    "#b279a2", "#ff9da6", "#9d755d", "#bab0ac",
]
FORMATION_COLORS: dict[str, str] = {f: _QUALITATIVE[i % len(_QUALITATIVE)] for i, f in enumerate(FORMATIONS)}
FORMATION_ORDER: dict[str, int] = {f: i for i, f in enumerate(FORMATIONS)}

SEVERITY_COLORS = {"low": "#2ca02c", "medium": "#f2b701", "high": "#e45756", "critical": "#8b0000"}

_SYMBOLS = ["circle", "square", "diamond", "triangle-up", "triangle-down", "star",
            "cross", "x", "pentagon", "hexagon", "hourglass", "bowtie"]
_EVENT_TYPES = [
    "mud_loss", "kick", "stuck_pipe", "overpressure", "torque_spike", "cementing_issue",
    "fishing_operation", "wellbore_instability", "gas_show", "twist_off", "lost_bha", "npt_other",
]
EVENT_SYMBOLS: dict[str, str] = {t: _SYMBOLS[i % len(_SYMBOLS)] for i, t in enumerate(_EVENT_TYPES)}


def _val(x):
    """Events/severities may be enum members (db rows) or plain strings — normalise to str."""
    return getattr(x, "value", x)


def _hover(ev: dict) -> str:
    return (
        f"<b>{_val(ev.get('type'))}</b> ({_val(ev.get('severity'))})<br>"
        f"page: {ev.get('page_ref')}<br>"
        f"cause: {ev.get('cause') or '-'}<br>"
        f"remedy: {ev.get('remedy') or '-'}<br>"
        f"NPT: {ev.get('hours_lost') if ev.get('hours_lost') is not None else '-'} h"
    )


def correlation_figure(panel_data: dict, mode: str = "tvd") -> go.Figure:
    """One column (subplot) per well. Formation bands via add_hrect (colour
    fixed per formation across the whole figure); event markers symbol=type,
    colour=severity, hover=page_ref+cause+remedy. Shared, reversed y-axis
    (depth increases downward).

    mode="tvd": bands/events at true TVD.
    mode="normalised": y = formation index + fractional offset-from-top
    within that formation's band, so wells align on formation regardless of
    true depth (see geo/correlate.py correlation_panel_data).
    """
    wells = list(panel_data.get("wells", {}).keys())
    if not wells:
        return go.Figure()

    fig = make_subplots(rows=1, cols=len(wells), shared_yaxes=True,
                         subplot_titles=wells, horizontal_spacing=0.03)

    pending_bands: list[tuple[int, float, float, str]] = []  # added after traces (see below)
    for col, wid in enumerate(wells, start=1):
        well_data = panel_data["wells"][wid]
        bands = well_data["bands"]

        for band in bands:
            formation = band["formation"]
            color = FORMATION_COLORS.get(formation, "#cccccc")
            if mode == "normalised":
                idx = FORMATION_ORDER.get(formation, len(FORMATION_ORDER))
                y0, y1 = idx, idx + 1
            else:
                y0 = band["top_tvd_m"]
                y1 = band["base_tvd_m"] if band["base_tvd_m"] is not None else y0 + 1.0
            pending_bands.append((col, y0, y1, color))

        by_severity: dict[str, dict] = {}
        for ev in well_data["events"]:
            if mode == "normalised":
                formation = ev.get("formation")
                band = next((b for b in bands if b["formation"] == formation), None)
                if band is None or ev.get("tvd") is None:
                    continue
                idx = FORMATION_ORDER.get(formation, len(FORMATION_ORDER))
                thickness = (band["base_tvd_m"] - band["top_tvd_m"]) if band["base_tvd_m"] else 1.0
                thickness = thickness or 1.0
                frac = min(max((ev["tvd"] - band["top_tvd_m"]) / thickness, 0.0), 1.0)
                y = idx + frac
            else:
                if ev.get("tvd") is None:
                    continue
                y = ev["tvd"]

            sev = _val(ev.get("severity")) or "medium"
            etype = _val(ev.get("type")) or "npt_other"
            trace = by_severity.setdefault(sev, {"x": [], "y": [], "text": [], "symbol": []})
            trace["x"].append(wid)
            trace["y"].append(y)
            trace["text"].append(_hover(ev))
            trace["symbol"].append(EVENT_SYMBOLS.get(etype, "circle"))

        # Anchor trace: a subplot column with no events gets no axes, and plotly then
        # silently discards every shape (band) targeted at it. One invisible point per
        # column guarantees the axes exist.
        fig.add_trace(
            go.Scatter(x=[wid], y=[None], mode="markers", marker=dict(opacity=0),
                       showlegend=False, hoverinfo="skip"),
            row=1, col=col,
        )
        for sev, trace in by_severity.items():
            fig.add_trace(
                go.Scatter(
                    x=trace["x"], y=trace["y"], mode="markers", name=sev,
                    legendgroup=sev, showlegend=(col == 1),
                    marker=dict(symbol=trace["symbol"], size=12,
                                color=SEVERITY_COLORS.get(sev, "#888"),
                                line=dict(width=1, color="#333")),
                    text=trace["text"], hovertemplate="%{text}<extra></extra>",
                ),
                row=1, col=col,
            )

        fig.update_xaxes(showticklabels=False, row=1, col=col)

    # Bands go in only now: add_hrect on a subplot with no trace yet is silently discarded by
    # plotly (found 26 Sep by the frontend build). Anchor to the axes explicitly.
    for col, y0, y1, color in pending_bands:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=color, opacity=0.25, line_width=0, layer="below",
                      row=1, col=col)

    if mode == "normalised":
        fig.update_yaxes(autorange="reversed", title_text="formation index + offset frac", row=1, col=1)
        fig.update_yaxes(autorange="reversed", row=1)
    else:
        fig.update_yaxes(autorange="reversed", title_text="TVD (m)", row=1, col=1)
        fig.update_yaxes(autorange="reversed", row=1)

    fig.update_layout(title=f"Correlation panel ({mode})", height=600, showlegend=True)
    return fig


def map_figure(wells: list, active_id: Optional[str] = None, radius_km: float = 3.0,
               candidates: Optional[list] = None) -> go.Figure:
    """Scattermapbox (open-street-map style, no token needed): per-well flag
    dots coloured by dominant event type (OffsetEye pattern), active well
    highlighted, offset radius drawn as a circle polygon, ranked candidates
    (from nearby.nearby_wells) marked distinctly.

    Plotly >= 6 renamed the trace/layout family from `Scattermapbox`/`mapbox`
    (Mapbox GL, token required for most styles) to `Scattermap`/`map`
    (MapLibre, no token ever required); we use whichever this install has so
    "open-street-map" always renders without a token either way.
    """
    from nwis import db

    if hasattr(go, "Scattermap"):
        scatter_cls, layout_key = go.Scattermap, "map"
    else:  # pragma: no cover - older plotly
        scatter_cls, layout_key = go.Scattermapbox, "mapbox"

    candidate_ids = {c.well_id for c in (candidates or [])}
    fig = go.Figure()

    lats, lons, texts, colors = [], [], [], []
    for w in wells:
        events = db.events_for([w.well_id])
        dominant = Counter(_val(e.event_type) for e in events).most_common(1)
        dom_type = dominant[0][0] if dominant else None
        # Deterministic colour per event type (stable across renders).
        color = _QUALITATIVE[abs(hash(dom_type)) % len(_QUALITATIVE)] if dom_type else "#b0b0b0"

        lats.append(w.lat)
        lons.append(w.lon)
        marker = "active well" if w.well_id == active_id else (
            "offset candidate" if w.well_id in candidate_ids else "well"
        )
        texts.append(f"{w.well_id} ({marker})<br>dominant event: {dom_type or 'none'}")
        colors.append(color)

    fig.add_trace(scatter_cls(
        lat=lats, lon=lons, mode="markers", text=texts,
        marker=dict(size=[16 if wid == active_id else 12 for wid in [w.well_id for w in wells]],
                    color=colors),
        hovertemplate="%{text}<extra></extra>", name="wells",
    ))

    if active_id is not None:
        active = db.get_well(active_id)
        if active is not None:
            ring = circle_polygon(active.lat, active.lon, radius_km)
            fig.add_trace(scatter_cls(
                lat=[p[0] for p in ring], lon=[p[1] for p in ring],
                mode="lines", line=dict(width=2, color="#e45756"),
                name=f"{radius_km} km radius",
            ))
            center_lat, center_lon = active.lat, active.lon
        else:
            center_lat = sum(lats) / len(lats) if lats else 0.0
            center_lon = sum(lons) / len(lons) if lons else 0.0
    else:
        center_lat = sum(lats) / len(lats) if lats else 0.0
        center_lon = sum(lons) / len(lons) if lons else 0.0

    fig.update_layout(
        **{layout_key: dict(style="open-street-map", center=dict(lat=center_lat, lon=center_lon), zoom=9)},
        height=600, margin=dict(l=0, r=0, t=30, b=0),
        title="Offset wells",
    )
    return fig
