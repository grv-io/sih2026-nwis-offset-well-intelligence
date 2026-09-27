# Predictive metrics (leave-wells-out, 5-fold GroupKFold)

Honesty note: XGBoost is compared against a formation-prior baseline and a precedent-density-only logistic baseline on the SAME held-out wells. No hyperparameter was tuned against these test folds.

| Hazard | n truth events | n positive intervals | Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|---|---|---|
| stuck_pipe | 35 | 126 | **XGBoost** | 0.883 +/- 0.043 | 0.606 +/- 0.077 | 0.038 +/- 0.013 |
| stuck_pipe | 35 | 126 | baseline: formation prior | 0.826 +/- 0.044 | 0.181 +/- 0.042 | 0.055 +/- 0.011 |
| stuck_pipe | 35 | 126 | baseline: precedent-density logistic | 0.510 +/- 0.012 | 0.076 +/- 0.021 | 0.245 +/- 0.002 |
| stuck_pipe | | | *verdict* | XGBoost beats the formation-prior baseline on held-out wells. | | |
| mud_loss | 37 | 119 | **XGBoost** | 0.858 +/- 0.056 | 0.585 +/- 0.043 | 0.037 +/- 0.015 |
| mud_loss | 37 | 119 | baseline: formation prior | 0.814 +/- 0.087 | 0.190 +/- 0.043 | 0.055 +/- 0.024 |
| mud_loss | 37 | 119 | baseline: precedent-density logistic | 0.544 +/- 0.030 | 0.091 +/- 0.046 | 0.241 +/- 0.005 |
| mud_loss | | | *verdict* | XGBoost beats the formation-prior baseline on held-out wells. | | |
| kick | 17 | 62 | **XGBoost** | 0.935 +/- 0.067 | 0.592 +/- 0.235 | 0.019 +/- 0.009 |
| kick | 17 | 62 | baseline: formation prior | 0.885 +/- 0.119 | 0.256 +/- 0.147 | 0.028 +/- 0.010 |
| kick | 17 | 62 | baseline: precedent-density logistic | 0.544 +/- 0.062 | 0.075 +/- 0.056 | 0.234 +/- 0.006 |
| kick | | | *verdict* | XGBoost beats the formation-prior baseline on held-out wells. | | |
| overpressure | 24 | 91 | **XGBoost** | 0.868 +/- 0.054 | 0.427 +/- 0.088 | 0.034 +/- 0.010 |
| overpressure | 24 | 91 | baseline: formation prior | 0.788 +/- 0.024 | 0.114 +/- 0.031 | 0.043 +/- 0.012 |
| overpressure | 24 | 91 | baseline: precedent-density logistic | 0.487 +/- 0.036 | 0.051 +/- 0.019 | 0.245 +/- 0.003 |
| overpressure | | | *verdict* | XGBoost beats the formation-prior baseline on held-out wells. | | |

Fixed-bin frequency-count floor (A8) saved separately to `models/risk_summary.json` (P(hazard | formation, 100 m offset-from-top bin), well-level, Laplace-smoothed, falls back to the global per-hazard rate when a bin has < 3 covering wells).