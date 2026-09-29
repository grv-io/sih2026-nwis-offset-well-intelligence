"""Phase 5 — `python -m nwis.predict.train`

Trains one XGBoost classifier per hazard (stuck_pipe, mud_loss, kick always; overpressure
only if it has >= 15 ground-truth events, per docs/IMPLEMENTATION_PLAN.md Phase 5 item 2),
evaluated with leave-wells-out GroupKFold (5 folds, grouped by well_id so no well ever
appears in both a fold's train and test split).

Next to every XGBoost fold metric we report two baselines (A10):
  (a) formation-prior baseline — P(hazard | formation), fit on the fold's TRAIN wells only,
      applied to the TEST wells (never leaks test-well outcomes into the estimate).
  (b) precedent-density-only logistic baseline — a 3-feature logistic regression
      (precedent count / hours-lost-sum / similarity-weighted density for that hazard).

We do not tune XGBoost against these test folds; the same fixed hyperparameters are used
for every hazard, and models/PREDICT_METRICS.md says plainly, per hazard, whether XGBoost
actually beat the formation prior on held-out wells.

Also fits the fixed-bin frequency-count floor (A8): P(hazard | formation, 100 m
offset-from-top bin), at WELL level (does this well have >=1 truth event of this hazard in
this bin, yes/no) to avoid pseudo-replication across a well's many 50 m intervals in the
same bin — saved to models/risk_summary.json.

Outputs:
  models/xgb_<hazard>.json          - final XGBoost model, fit on all wells
  models/xgb_<hazard>_meta.json     - feature column order, formation categories,
                                       isotonic calibration knots, n_truth_events
  models/predict_metrics.json       - full per-fold + summary metrics, all hazards
  models/PREDICT_METRICS.md         - human-readable table
  models/risk_summary.json          - A8 frequency-count floor
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from nwis import db
from nwis.config import settings
from nwis.predict import features as F
from nwis.schema import FORMATIONS

N_FOLDS = 5
MIN_EVENTS_FOR_OPTIONAL_HAZARD = 15
BIN_WIDTH_M = 100.0
MIN_WELLS_PER_BIN = 3

XGB_PARAMS = dict(
    n_estimators=150, max_depth=4, learning_rate=0.08, subsample=0.8,
    colsample_bytree=0.8, min_child_weight=3, eval_metric="logloss",
    random_state=42, n_jobs=-1,
)

MODELS_DIR = settings.models_dir


def _truth_event_count(hazard: str) -> int:
    return sum(
        1 for e in db.events_for(event_type=hazard)
        if e.extraction_method == "synthetic_truth"
    )


def _hazards_to_train() -> list[str]:
    hazards = ["stuck_pipe", "mud_loss", "kick"]
    if _truth_event_count("overpressure") >= MIN_EVENTS_FOR_OPTIONAL_HAZARD:
        hazards.append("overpressure")
    return hazards


def _feature_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """One-hot encode `formation` (fixed category set = nwis.schema.FORMATIONS) and return
    (augmented df, ordered list of model feature column names)."""
    df = df.copy()
    for f in FORMATIONS:
        df[f"formation_{f}"] = (df["formation"] == f).astype(float)
    exclude = {"well_id", "top_md_m", "base_md_m", "mid_md_m", "formation"}
    exclude |= {c for c in df.columns if c.startswith("label_")}
    feature_cols = [c for c in df.columns if c not in exclude]
    return df, feature_cols


def _formation_prior_baseline(train_df: pd.DataFrame, test_df: pd.DataFrame, label_col: str) -> np.ndarray:
    global_rate = float(train_df[label_col].mean())
    rate_by_formation = train_df.groupby("formation")[label_col].mean().to_dict()
    return test_df["formation"].map(rate_by_formation).fillna(global_rate).to_numpy(dtype=float)


def _precedent_logistic_baseline(train_df, test_df, hazard: str, label_col: str) -> np.ndarray:
    cols = [f"precedent_count_{hazard}", f"precedent_hours_{hazard}", f"precedent_simw_{hazard}"]
    Xtr = train_df[cols].fillna(0.0).to_numpy(dtype=float)
    Xte = test_df[cols].fillna(0.0).to_numpy(dtype=float)
    ytr = train_df[label_col].to_numpy(dtype=int)
    if len(np.unique(ytr)) < 2:
        # degenerate fold (no positives in train) -> constant prediction at the train rate
        return np.full(len(test_df), float(ytr.mean()))
    scaler = StandardScaler().fit(Xtr)
    clf = LogisticRegression(max_iter=500, class_weight="balanced")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf.fit(scaler.transform(Xtr), ytr)
    return clf.predict_proba(scaler.transform(Xte))[:, 1]


def _safe_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    out = {"roc_auc": None, "pr_auc": None, "brier": None}
    if len(np.unique(y_true)) < 2:
        return out  # undefined on this fold; aggregation ignores None
    out["roc_auc"] = float(roc_auc_score(y_true, y_prob))
    out["pr_auc"] = float(average_precision_score(y_true, y_prob))
    out["brier"] = float(brier_score_loss(y_true, y_prob))
    return out


def _mean_std(values: list[float]) -> dict:
    vals = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if not vals:
        return {"mean": None, "std": None, "n_folds": 0}
    return {"mean": float(np.mean(vals)), "std": float(np.std(vals)), "n_folds": len(vals)}


def train_hazard(df: pd.DataFrame, hazard: str) -> dict:
    label_col = f"label_{hazard}"
    df_aug, feature_cols = _feature_columns(df)
    wells = df_aug["well_id"].to_numpy()
    y_all = df_aug[label_col].to_numpy(dtype=int)
    X_all = df_aug[feature_cols]

    n_folds = min(N_FOLDS, df_aug["well_id"].nunique())
    gkf = GroupKFold(n_splits=n_folds)

    fold_results = {"xgboost": [], "formation_prior": [], "precedent_logistic": []}
    oof_prob = np.full(len(df_aug), np.nan)

    for fold_i, (tr_idx, te_idx) in enumerate(gkf.split(X_all, y_all, groups=wells)):
        train_df, test_df = df_aug.iloc[tr_idx], df_aug.iloc[te_idx]
        y_tr, y_te = y_all[tr_idx], y_all[te_idx]

        clf = XGBClassifier(**XGB_PARAMS)
        clf.fit(train_df[feature_cols], y_tr)
        p_xgb = clf.predict_proba(test_df[feature_cols])[:, 1]
        oof_prob[te_idx] = p_xgb

        p_prior = _formation_prior_baseline(train_df, test_df, label_col)
        p_prec = _precedent_logistic_baseline(train_df, test_df, hazard, label_col)

        fold_results["xgboost"].append({"fold": fold_i, **_safe_metrics(y_te, p_xgb), "n_test": int(len(y_te)), "n_pos_test": int(y_te.sum())})
        fold_results["formation_prior"].append({"fold": fold_i, **_safe_metrics(y_te, p_prior)})
        fold_results["precedent_logistic"].append({"fold": fold_i, **_safe_metrics(y_te, p_prec)})

    summary = {}
    for model_name, folds in fold_results.items():
        summary[model_name] = {
            "roc_auc": _mean_std([f["roc_auc"] for f in folds]),
            "pr_auc": _mean_std([f["pr_auc"] for f in folds]),
            "brier": _mean_std([f["brier"] for f in folds]),
        }

    xgb_auc = summary["xgboost"]["roc_auc"]["mean"]
    prior_auc = summary["formation_prior"]["roc_auc"]["mean"]
    beats_prior = (xgb_auc is not None and prior_auc is not None and xgb_auc > prior_auc)

    # isotonic calibration on out-of-fold predictions (every row predicted exactly once,
    # by a model that never saw its own well in training)
    mask = ~np.isnan(oof_prob)
    calibration = {"x": [], "y": []}
    brier_oof_raw, brier_oof_cal = None, None
    if mask.sum() > 10 and len(np.unique(y_all[mask])) == 2:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(oof_prob[mask], y_all[mask])
        calibration = {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}
        brier_oof_raw = float(brier_score_loss(y_all[mask], oof_prob[mask]))
        brier_oof_cal = float(brier_score_loss(y_all[mask], iso.predict(oof_prob[mask])))

    # final model: fit on ALL wells (this is what risk_ahead.py loads for scoring)
    final_clf = XGBClassifier(**XGB_PARAMS)
    final_clf.fit(X_all, y_all)
    model_path = MODELS_DIR / f"xgb_{hazard}.json"
    final_clf.save_model(str(model_path))

    n_truth = _truth_event_count(hazard)
    meta = {
        "hazard": hazard,
        "feature_columns": feature_cols,
        "formations": FORMATIONS,
        "n_truth_events": n_truth,
        "n_rows": int(len(df_aug)),
        "n_positive_rows": int(y_all.sum()),
        "calibration_isotonic": calibration,
        "brier_oof_raw": brier_oof_raw,
        "brier_oof_calibrated": brier_oof_cal,
    }
    meta_path = MODELS_DIR / f"xgb_{hazard}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    return {
        "hazard": hazard,
        "n_truth_events": n_truth,
        "n_rows": int(len(df_aug)),
        "n_positive_rows": int(y_all.sum()),
        "n_folds": n_folds,
        "folds": fold_results,
        "summary": summary,
        "beats_formation_prior": beats_prior,
        # repo-relative, so the committed metrics carry no machine-specific paths
        "model_path": f"models/{model_path.name}",
        "meta_path": f"models/{meta_path.name}",
    }


def build_risk_summary(df: pd.DataFrame) -> dict:
    """A8 fixed-bin frequency-count floor: P(hazard | formation, 100 m offset-from-top
    bin), at WELL level. `df` is the interval feature table (used only for the
    well/formation/bin coverage denominator); numerators come straight from the DB's
    ground-truth events."""
    all_wells = db.all_wells()
    truth_events = [e for e in db.events_for() if e.extraction_method == "synthetic_truth"]

    # coverage[(formation, bin_start)] = set of well_ids whose interval table has a row
    # with that formation in that bin (i.e. the well penetrates that stage of that formation)
    coverage: dict[tuple, set] = {}
    for _, row in df.iterrows():
        formation, offset = row["formation"], row["offset_from_top_m"]
        if formation is None or offset is None or (isinstance(offset, float) and np.isnan(offset)):
            continue
        bin_start = float(np.floor(offset / BIN_WIDTH_M) * BIN_WIDTH_M)
        coverage.setdefault((formation, bin_start), set()).add(row["well_id"])

    # positives[(formation, bin_start, hazard)] = set of well_ids with >=1 truth event there
    positives: dict[tuple, set] = {}
    for e in truth_events:
        if e.formation is None:
            continue
        offset = e.formation_offset_from_top_m
        if offset is None:
            continue
        bin_start = float(np.floor(offset / BIN_WIDTH_M) * BIN_WIDTH_M)
        positives.setdefault((e.formation, bin_start, e.event_type), set()).add(e.well_id)

    hazards = sorted({e.event_type for e in truth_events})
    global_rate = {h: sum(1 for e in truth_events if e.event_type == h) / max(1, len(all_wells)) for h in hazards}

    summary: dict = {}
    for (formation, bin_start), well_set in coverage.items():
        n_wells = len(well_set)
        summary.setdefault(formation, {})[str(bin_start)] = {}
        for h in hazards:
            n_pos = len(positives.get((formation, bin_start, h), set()) & well_set)
            if n_wells >= MIN_WELLS_PER_BIN:
                p = (n_pos + 1) / (n_wells + 2)  # Laplace-smoothed
            else:
                p = global_rate.get(h, 0.0)
            summary[formation][str(bin_start)][str(h)] = {
                "p": round(float(p), 4), "n_wells": n_wells, "n_pos_wells": n_pos,
            }
    return summary


def _markdown_table(results: list[dict]) -> str:
    lines = [
        "# Phase 5 predictive metrics (leave-wells-out, 5-fold GroupKFold)",
        "",
        "Honesty note: XGBoost is compared against a formation-prior baseline and a "
        "precedent-density-only logistic baseline on the SAME held-out wells. No "
        "hyperparameter was tuned against these test folds.",
        "",
        "| Hazard | n truth events | n positive intervals | Model | ROC-AUC | PR-AUC | Brier |",
        "|---|---|---|---|---|---|---|",
    ]

    def fmt(d):
        if d is None or d.get("mean") is None:
            return "n/a"
        return f"{d['mean']:.3f} +/- {d['std']:.3f}"

    for r in results:
        h = r["hazard"]
        for model_name, label in [("xgboost", "**XGBoost**"), ("formation_prior", "baseline: formation prior"),
                                    ("precedent_logistic", "baseline: precedent-density logistic")]:
            s = r["summary"][model_name]
            lines.append(
                f"| {h} | {r['n_truth_events']} | {r['n_positive_rows']} | {label} | "
                f"{fmt(s['roc_auc'])} | {fmt(s['pr_auc'])} | {fmt(s['brier'])} |"
            )
        verdict = "beats" if r["beats_formation_prior"] else "does NOT beat"
        lines.append(f"| {h} | | | *verdict* | XGBoost {verdict} the formation-prior baseline on held-out wells. | | |")
    lines.append("")
    lines.append("Fixed-bin frequency-count floor (A8) saved separately to `models/risk_summary.json` "
                  "(P(hazard | formation, 100 m offset-from-top bin), well-level, Laplace-smoothed, "
                  f"falls back to the global per-hazard rate when a bin has < {MIN_WELLS_PER_BIN} covering wells).")
    return "\n".join(lines)


LOG_STAT_COLS: list[str] = (
    [f"{F.LOG_PARAM_SHORT[p]}_mean" for p in F.LOG_PARAMS]
    + [f"{F.LOG_PARAM_SHORT[p]}_max" for p in F.LOG_PARAMS]
    + [f"{F.LOG_PARAM_SHORT[p]}_slope" for p in F.LOG_PARAMS]
    + ["pit_delta", "flow_delta", "mw_actual_mean"]
)


def build_formation_log_defaults(df: pd.DataFrame) -> dict:
    """Per-formation mean of every live-parameter feature, across all wells that have
    already drilled through it. risk_ahead.py's ahead-of-bit scoring fills these in
    instead of leaving live-param features as NaN: NaN is honest for training (that
    interval really wasn't drilled — vanishingly rare, since these synthetic logs cover
    every well end-to-end) but out-of-distribution at inference (every ahead-of-bit
    interval would be all-NaN simultaneously, a pattern XGBoost essentially never saw
    during training and can extrapolate wildly on). A "typical for this formation" value
    is the honest middle ground between "pretend we already drilled it" and "feed the
    model something the training data never demonstrated any behaviour for."
    """
    global_defaults = df[LOG_STAT_COLS].mean(numeric_only=True).to_dict()
    by_formation = df.groupby("formation")[LOG_STAT_COLS].mean(numeric_only=True)
    out = {"_global": {k: float(v) for k, v in global_defaults.items()}}
    for formation, row in by_formation.iterrows():
        out[str(formation)] = {k: (float(v) if pd.notna(v) else out["_global"].get(k, 0.0)) for k, v in row.items()}
    return out


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    df = F.build_all(force=True)
    print(f"features: {df.shape[0]} rows x {df.shape[1]} cols")

    formation_log_defaults = build_formation_log_defaults(df)
    (MODELS_DIR / "formation_log_defaults.json").write_text(json.dumps(formation_log_defaults, indent=2))

    hazards = _hazards_to_train()
    print("training hazards:", hazards)

    results = []
    for h in hazards:
        print(f"--- {h} ---")
        r = train_hazard(df, h)
        results.append(r)
        print(f"  n_truth_events={r['n_truth_events']} n_positive_rows={r['n_positive_rows']} "
              f"xgb_auc={r['summary']['xgboost']['roc_auc']} prior_auc={r['summary']['formation_prior']['roc_auc']} "
              f"beats_prior={r['beats_formation_prior']}")

    risk_summary = build_risk_summary(df)
    (MODELS_DIR / "risk_summary.json").write_text(json.dumps(risk_summary, indent=2))

    metrics_out = {
        "interval_m": settings.interval_m,
        "lookahead_intervals": settings.lookahead_intervals,
        "n_folds_requested": N_FOLDS,
        "hazards": results,
    }
    (MODELS_DIR / "predict_metrics.json").write_text(json.dumps(metrics_out, indent=2))
    (MODELS_DIR / "PREDICT_METRICS.md").write_text(_markdown_table(results))

    print("\nWrote models/predict_metrics.json, models/PREDICT_METRICS.md, models/risk_summary.json")
    print(f"\n{_markdown_table(results)}")


if __name__ == "__main__":
    main()
