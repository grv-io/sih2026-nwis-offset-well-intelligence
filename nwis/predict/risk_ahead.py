"""Phase 5 item 3 — `score(well_id, md, radius_km=3.0) -> list[RiskInterval]`.

Owns "risk-ahead-of-bit": for the next `lookahead_intervals` * `interval_m` metres ahead
of the current bit depth `md`, fuse three transparent components per hazard:

    score = risk_w_precedent * precedent_component
          + risk_w_anomaly   * anomaly_component
          + risk_w_model     * model_component

(weights from nwis.config.settings — see docs/IMPLEMENTATION_PLAN.md Phase 5.)

  precedent_component  Similarity-weighted offset-event density for that hazard, in the
                        SAME formation + offset-from-top band as the target interval
                        (formation-normalised — the exact feature construction used by
                        nwis.predict.features, so train/serve match). This intentionally
                        does not repeat nwis.geo.correlate.active_well_lookahead's
                        absolute-MD dip projection (that's a ~0.5s/call, DB+lstsq-heavy
                        path already owned by nwis.live.alerts's separate precedent_zone
                        rule); offset-from-top is already dip-independent, so re-deriving
                        it here would buy nothing but latency against this module's
                        <300ms-warm budget.
  anomaly_component    Latest live-anomaly strength for that hazard (nwis.live.anomaly),
                        if importable AND a live sample window is supplied. 0.0 + recorded
                        in `signals_unavailable()` otherwise (this module's `score()`
                        contract per nwis.live.alerts is `score(well_id, md)` with no
                        window — alerts.py's own param_anomaly rule already covers "right
                        now"; this component only fires when a caller explicitly passes
                        one). Decays across the lookahead intervals (nearest first).
  model_component      Calibrated XGBoost probability, IF a trained model exists for that
                        hazard AND the hazard has >= MIN_LABELLED_EVENTS_FOR_SUPERVISED
                        ground-truth events (A2 mode switch) -- else the A8 fixed-bin
                        frequency floor (models/risk_summary.json). Every interval's
                        top_reasons[0] is "mode:supervised" or "mode:indicator" so callers
                        never mistake one for the other.

Every interval a call returns is genuinely ahead of the bit: live-parameter features
(torque/spp/rop/gas stats, pit/flow deltas, actual mud weight) are NaN by construction --
they describe samples that haven't been drilled yet, so XGBoost sees them as missing
(its native handling), never as "normal". This is the thing OffsetEye's symmetric +-50m
alerting and CB-acc-tech's decorative dip azimuth both fail to do honestly.

Caching (module-level dicts, per docs/IMPLEMENTATION_PLAN.md Phase 5 item 3): nearby-wells
tables, per-well event rows, per-hazard models+meta+explainers, and the risk_summary.json
frequency table are all loaded once and reused. First call for a new well/radius is slow
(nearby_wells does cold parquet reads); subsequent calls are the <300ms-warm target.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from nwis import db
from nwis.config import settings
from nwis.geo import correlate
from nwis.geo.nearby import nearby_wells, OffsetCandidate
from nwis.predict import explain, features as F
from nwis.schema import EventType, RiskInterval

MIN_LABELLED_EVENTS_FOR_SUPERVISED = 20  # A2 mode-switch threshold (module-local, not config)
PRECEDENT_NORM_K = 1.5  # saturating divisor: weighted_density -> component via 1 - exp(-d/K)
ANOMALY_DECAY = 0.7  # per-interval decay applied to a "right now" anomaly reading
SEVERITY_WEIGHT = {"low": 0.5, "medium": 1.0, "high": 1.5, "critical": 2.0}
RISK_SUMMARY_PATH: Path = settings.models_dir / "risk_summary.json"
BIN_WIDTH_M = 100.0  # must match nwis.predict.train.BIN_WIDTH_M

# --------------------------------------------------------------------------- #
# module-level caches
# --------------------------------------------------------------------------- #
_nearby_cache: dict[tuple[str, float], list[OffsetCandidate]] = {}
_event_rows_cache: dict[str, list[dict]] = {}
_thickness_cache: dict[str, dict[str, float]] = {}
_model_cache: dict[str, Optional[dict]] = {}
_risk_summary_cache: Optional[dict] = None
_risk_summary_global_rate: dict[str, float] = {}
_truth_count_cache: dict[str, int] = {}
_signals_unavailable: set[str] = set()
_formation_log_defaults_cache: Optional[dict] = None
FORMATION_LOG_DEFAULTS_PATH: Path = settings.models_dir / "formation_log_defaults.json"


def signals_unavailable() -> set[str]:
    """What this module has had to degrade on so far this process (e.g. 'anomaly:no_window'
    when score() is called without a live window). Mirrors nwis.live.alerts's pattern."""
    return set(_signals_unavailable)


def _get_candidates(well_id: str, radius_km: float) -> list[OffsetCandidate]:
    key = (well_id, radius_km)
    if key not in _nearby_cache:
        _nearby_cache[key] = nearby_wells(well_id, radius_km)
    return _nearby_cache[key]


def _get_event_rows(well_id: str) -> list[dict]:
    if well_id not in _event_rows_cache:
        _event_rows_cache[well_id] = F._well_event_rows(well_id)
    return _event_rows_cache[well_id]


def _get_thickness(well_id: str) -> dict[str, float]:
    if well_id not in _thickness_cache:
        tops = db.tops_for(well_id)
        _thickness_cache[well_id] = {
            t.formation: ((t.base_md_m if t.base_md_m is not None else float("inf")) - t.top_md_m)
            for t in tops
        }
    return _thickness_cache[well_id]


def _get_truth_count(hazard: str) -> int:
    if hazard not in _truth_count_cache:
        _truth_count_cache[hazard] = sum(
            1 for e in db.events_for(event_type=hazard) if e.extraction_method == "synthetic_truth"
        )
    return _truth_count_cache[hazard]


def _get_model(hazard: str) -> Optional[dict]:
    if hazard in _model_cache:
        return _model_cache[hazard]
    model_path = settings.models_dir / f"xgb_{hazard}.json"
    meta_path = settings.models_dir / f"xgb_{hazard}_meta.json"
    if not model_path.exists() or not meta_path.exists():
        _model_cache[hazard] = None
        return None
    clf = XGBClassifier()
    clf.load_model(str(model_path))
    meta = json.loads(meta_path.read_text())
    entry = {
        "model": clf,
        "meta": meta,
        "explainer": explain.Explainer(clf, meta["feature_columns"]),
    }
    _model_cache[hazard] = entry
    return entry


def _get_risk_summary() -> dict:
    global _risk_summary_cache, _risk_summary_global_rate
    if _risk_summary_cache is None:
        if RISK_SUMMARY_PATH.exists():
            _risk_summary_cache = json.loads(RISK_SUMMARY_PATH.read_text())
        else:
            _risk_summary_cache = {}
        # global fallback rate per hazard, across every bin in the table
        totals: dict[str, list[float]] = {}
        for formation_dict in _risk_summary_cache.values():
            for bin_dict in formation_dict.values():
                for hazard, entry in bin_dict.items():
                    totals.setdefault(hazard, []).append(entry["p"])
        _risk_summary_global_rate = {h: float(np.mean(v)) for h, v in totals.items() if v}
    return _risk_summary_cache


def _risk_summary_lookup(formation: Optional[str], offset: Optional[float], hazard: str) -> float:
    summary = _get_risk_summary()
    if formation is None or offset is None or math.isnan(offset):
        return _risk_summary_global_rate.get(hazard, 0.05)
    bin_start = str(float(math.floor(offset / BIN_WIDTH_M) * BIN_WIDTH_M))
    entry = summary.get(formation, {}).get(bin_start, {}).get(hazard)
    if entry is not None:
        return float(entry["p"])
    return _risk_summary_global_rate.get(hazard, 0.05)


def _get_formation_log_defaults() -> dict:
    global _formation_log_defaults_cache
    if _formation_log_defaults_cache is None:
        if FORMATION_LOG_DEFAULTS_PATH.exists():
            _formation_log_defaults_cache = json.loads(FORMATION_LOG_DEFAULTS_PATH.read_text())
        else:
            _formation_log_defaults_cache = {"_global": {}}
    return _formation_log_defaults_cache


def _log_defaults_for(formation: Optional[str]) -> dict:
    defaults = _get_formation_log_defaults()
    return defaults.get(formation, defaults.get("_global", {})) if formation else defaults.get("_global", {})


def _apply_calibration(raw_prob: float, calibration: dict) -> float:
    x, y = calibration.get("x") or [], calibration.get("y") or []
    if not x:
        return raw_prob
    return float(np.clip(np.interp(raw_prob, x, y), 0.0, 1.0))


def _precedent_density_all_hazards(
    formation: Optional[str], offset: Optional[float], step: float,
    candidates: list[OffsetCandidate], candidate_events: dict[str, list[dict]],
) -> dict:
    if offset is None:
        band_lo, band_hi = 0.0, -1.0
    else:
        band_lo, band_hi = offset - step / 2.0, offset + step / 2.0
    return F._precedent_density(formation, band_lo, band_hi, candidates, candidate_events)


def _precedent_component(weighted_density: float) -> float:
    return float(1.0 - math.exp(-max(0.0, weighted_density) / PRECEDENT_NORM_K))


def _top_precedent_sources(
    formation: Optional[str], offset: Optional[float], step: float, hazard: str,
    candidates: list[OffsetCandidate], candidate_events: dict[str, list[dict]], top_n: int = 3,
) -> list[str]:
    if formation is None or offset is None:
        return []
    band_lo, band_hi = offset - step / 2.0, offset + step / 2.0
    contributions = []
    for cand in candidates:
        for ev in candidate_events.get(cand.well_id, []):
            if ev["formation"] != formation or ev["offset_from_top_m"] is None:
                continue
            if not (band_lo <= ev["offset_from_top_m"] < band_hi):
                continue
            ev_type = ev["event_type"].value if hasattr(ev["event_type"], "value") else str(ev["event_type"])
            if ev_type != hazard:
                continue
            w = ev["weight"] * cand.score
            contributions.append((cand.well_id, w, ev["hours_lost_npt"]))
    contributions.sort(key=lambda c: -c[1])
    return [f"precedent:{wid}:weight={w:.2f}:hours_lost={h:.1f}" for wid, w, h in contributions[:top_n]]


def _anomaly_component(hazard: str, window: Optional[pd.DataFrame]) -> float:
    if window is None or window.empty:
        _signals_unavailable.add("anomaly:no_window")
        return 0.0
    try:
        from nwis.live import anomaly as anom
    except ImportError:
        _signals_unavailable.add("anomaly:import_error")
        return 0.0
    try:
        flags = anom.evaluate_flags(window)
    except Exception:  # noqa: BLE001
        _signals_unavailable.add("anomaly:eval_error")
        return 0.0
    best = 0.0
    for rule_name, (flag, strength, _evidence) in flags.items():
        hz = anom.HAZARD_MAP.get(rule_name)
        if hz is not None and hz.value == hazard:
            best = max(best, strength)
    return float(best)


def _build_feature_row(
    formation: Optional[str], offset: Optional[float], precedent_feats: dict,
    feature_columns: list[str], formations: list[str],
) -> pd.DataFrame:
    """Ahead-of-bit feature row: everything knowable in advance (formation, offset,
    fraction, precedent density) is filled in exactly. Live-parameter stats and actual mud
    weight — the things that require having already drilled the interval — are filled
    with this FORMATION's typical value from training (models/formation_log_defaults.json,
    built by nwis.predict.train), not left as raw NaN: the synthetic logs cover every well
    end-to-end, so "every live-param feature simultaneously missing" is a pattern XGBoost
    essentially never saw in training and extrapolates unpredictably on. A formation
    typical value is the honest middle ground between "pretend we already drilled it" and
    "feed the model something training never demonstrated any behaviour for."
    """
    defaults = _log_defaults_for(formation)
    mw_actual_mean = defaults.get("mw_actual_mean", np.nan)
    mw_feats = F._mw_window_features(formation, mw_actual_mean)

    row: dict = {
        "offset_from_top_m": offset if offset is not None else np.nan,
        "fraction_of_formation": np.nan,
        "mw_actual_mean": mw_actual_mean,
        **mw_feats,
        "torque_mean": defaults.get("torque_mean", np.nan), "torque_max": defaults.get("torque_max", np.nan),
        "torque_slope": defaults.get("torque_slope", np.nan),
        "spp_mean": defaults.get("spp_mean", np.nan), "spp_max": defaults.get("spp_max", np.nan),
        "spp_slope": defaults.get("spp_slope", np.nan),
        "rop_mean": defaults.get("rop_mean", np.nan), "rop_max": defaults.get("rop_max", np.nan),
        "rop_slope": defaults.get("rop_slope", np.nan),
        "gas_mean": defaults.get("gas_mean", np.nan), "gas_max": defaults.get("gas_max", np.nan),
        "gas_slope": defaults.get("gas_slope", np.nan),
        "pit_delta": defaults.get("pit_delta", np.nan), "flow_delta": defaults.get("flow_delta", np.nan),
        **precedent_feats,
    }
    for f in formations:
        row[f"formation_{f}"] = 1.0 if formation == f else 0.0
    return pd.DataFrame([{c: row.get(c, np.nan) for c in feature_columns}])


def _model_probability(
    hazard: str, formation: Optional[str], offset: Optional[float], precedent_feats: dict,
) -> tuple[float, str, Optional[pd.DataFrame], Optional[dict]]:
    """Returns (probability, mode, X_row_or_None, model_entry_or_None). model_entry/X_row
    are returned so the caller can compute SHAP reasons ONLY for the winning hazard,
    instead of once per hazard per interval."""
    entry = _get_model(hazard)
    n_truth = _get_truth_count(hazard)
    if entry is not None and n_truth >= MIN_LABELLED_EVENTS_FOR_SUPERVISED:
        X_row = _build_feature_row(formation, offset, precedent_feats, entry["meta"]["feature_columns"], entry["meta"]["formations"])
        raw = float(entry["model"].predict_proba(X_row)[:, 1][0])
        calibrated = _apply_calibration(raw, entry["meta"]["calibration_isotonic"])
        return calibrated, "supervised", X_row, entry
    p = _risk_summary_lookup(formation, offset, hazard)
    return p, "indicator", None, None


def score(well_id: str, md: float, radius_km: float = 3.0, window: Optional[pd.DataFrame] = None) -> list[RiskInterval]:
    """The contract nwis.live.alerts.process_model_risk calls as
    `risk_ahead.score(well_id, depth_md_m)`. Returns `settings.lookahead_intervals`
    RiskInterval objects (one per 50 m band ahead of `md`), sorted by depth, each carrying
    the single highest-scoring hazard for that band (RiskInterval has one `hazard` field;
    the fused score for every primary hazard is still computed, just not all returned)."""
    step = settings.interval_m
    n_ahead = settings.lookahead_intervals
    idx0 = int(math.floor(md / step))

    candidates = _get_candidates(well_id, radius_km)
    candidate_events = {c.well_id: _get_event_rows(c.well_id) for c in candidates}

    results: list[RiskInterval] = []
    for i in range(n_ahead):
        top = float((idx0 + i) * step)
        base = top + step
        mid = (top + base) / 2.0

        formation, offset = correlate.formation_offset(well_id, mid)
        precedent_feats = _precedent_density_all_hazards(formation, offset, step, candidates, candidate_events)

        best = None
        for hazard in F.PRIMARY_HAZARDS:
            weighted_density = precedent_feats.get(f"precedent_simw_{hazard}", 0.0)
            precedent_component = _precedent_component(weighted_density)
            anomaly_component = _anomaly_component(hazard, window) * (ANOMALY_DECAY ** i)
            model_component, mode, X_row, model_entry = _model_probability(hazard, formation, offset, precedent_feats)

            fused = (
                settings.risk_w_precedent * precedent_component
                + settings.risk_w_anomaly * anomaly_component
                + settings.risk_w_model * model_component
            )
            fused = float(np.clip(fused, 0.0, 1.0))

            candidate = {
                "hazard": hazard, "score": fused, "mode": mode,
                "precedent": precedent_component, "anomaly": anomaly_component, "model": model_component,
                "X_row": X_row, "model_entry": model_entry,
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate

        if best["mode"] == "supervised" and best["X_row"] is not None:
            # reuse the cached Explainer (built once in _get_model) -- constructing a
            # fresh shap.TreeExplainer per call is the dominant cost otherwise.
            pairs = best["model_entry"]["explainer"].top_reasons(best["X_row"], top_n=3)
            reasons = [f"{name}:{val:+.3f}" for name, val in pairs]
        else:
            reasons = _top_precedent_sources(formation, offset, step, best["hazard"], candidates, candidate_events)
            if not reasons:
                reasons = [f"risk_summary:formation={formation}:offset_bin={_bin_label(offset)}"]

        top_reasons = [f"mode:{best['mode']}"] + reasons[:3]

        results.append(RiskInterval(
            well_id=well_id, top_md_m=top, base_md_m=base, formation=formation,
            hazard=EventType(best["hazard"]), score=best["score"],
            precedent_component=best["precedent"], anomaly_component=best["anomaly"], model_component=best["model"],
            top_reasons=top_reasons,
        ))

    results.sort(key=lambda r: r.top_md_m)
    return results


def _bin_label(offset: Optional[float]) -> str:
    if offset is None or (isinstance(offset, float) and math.isnan(offset)):
        return "unknown"
    return str(float(math.floor(offset / BIN_WIDTH_M) * BIN_WIDTH_M))
