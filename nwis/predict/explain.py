"""SHAP explainability wrapper for the Phase 5 XGBoost models, with a graceful fallback to
plain XGBoost gain importances if `shap` fails to import or errors on a given model/row
(some shap versions are picky about the exact xgboost build). Never raises: risk_ahead.py
depends on this always returning *something* usable for top_reasons.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

try:
    import shap
    _SHAP_AVAILABLE = True
except ImportError:  # pragma: no cover
    shap = None  # type: ignore[assignment]
    _SHAP_AVAILABLE = False


def _gain_fallback(model, X_row: pd.DataFrame, feature_cols: list[str], top_n: int = 3) -> list[tuple[str, float]]:
    """Signless fallback: top-N features by the model's overall gain importance. Not a
    per-row explanation (gain importance is global), but always available."""
    booster = model.get_booster()
    score = booster.get_score(importance_type="gain")
    # booster feature names are f0, f1, ... unless set; map back to feature_cols by index
    named = {}
    for k, v in score.items():
        if k.startswith("f") and k[1:].isdigit():
            idx = int(k[1:])
            if idx < len(feature_cols):
                named[feature_cols[idx]] = v
        else:
            named[k] = v
    ranked = sorted(named.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [(name, float(val)) for name, val in ranked]


class Explainer:
    """One TreeExplainer per model, cached on first use (module-level cache lives in
    risk_ahead.py, which owns model lifecycle; this class just wraps a single model)."""

    def __init__(self, model, feature_cols: list[str]):
        self.model = model
        self.feature_cols = feature_cols
        self._explainer = None
        self._available = _SHAP_AVAILABLE
        if self._available:
            try:
                self._explainer = shap.TreeExplainer(model)
            except Exception:  # noqa: BLE001 - fall back honestly
                self._available = False
                self._explainer = None

    def top_reasons(self, X_row: pd.DataFrame, top_n: int = 3) -> list[tuple[str, float]]:
        """Top-N (feature_name, signed_contribution) pairs for one row (a 1-row DataFrame
        with `self.feature_cols` columns). Falls back to unsigned gain importances (value
        stored as positive float) if SHAP is unavailable or errors."""
        if self._available and self._explainer is not None:
            try:
                sv = self._explainer.shap_values(X_row)
                sv = np.asarray(sv)
                if sv.ndim == 3:  # some shap versions: (n_classes, n_rows, n_features)
                    sv = sv[-1]
                row_sv = sv[0] if sv.ndim == 2 else sv
                order = np.argsort(-np.abs(row_sv))[:top_n]
                return [(self.feature_cols[i], float(row_sv[i])) for i in order]
            except Exception:  # noqa: BLE001
                pass
        return _gain_fallback(self.model, X_row, self.feature_cols, top_n=top_n)


def explain_row(model, feature_cols: list[str], X_row: pd.DataFrame, top_n: int = 3) -> list[str]:
    """Convenience one-shot helper: formats top_n reasons as signed strings, e.g.
    'precedent_count_stuck_pipe:+0.42'. Used by risk_ahead.py's `top_reasons` output."""
    ex = Explainer(model, feature_cols)
    reasons = ex.top_reasons(X_row, top_n=top_n)
    return [f"{name}:{val:+.3f}" for name, val in reasons]
