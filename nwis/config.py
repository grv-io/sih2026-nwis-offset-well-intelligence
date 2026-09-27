"""Central config. Reads .env (see .env.example). Import `settings` everywhere."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


class Settings:
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    synthetic_dir: Path = ROOT / "data" / "synthetic"
    external_dir: Path = ROOT / "data" / "external"
    models_dir: Path = ROOT / "models"

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    db_path: Path = ROOT / os.getenv("NWIS_DB", "data/nwis.sqlite")

    # Risk fusion weights (Phase 5). Transparent, documented, tunable.
    risk_w_precedent: float = 0.5
    risk_w_anomaly: float = 0.3
    risk_w_model: float = 0.2
    interval_m: float = 50.0
    lookahead_intervals: int = 4

    # Alerting (Phase 6)
    alert_n_of_m: tuple[int, int] = (3, 5)
    alert_dwell_s: float = 20.0
    alert_cooldown_s: float = 120.0


settings = Settings()
for _d in (settings.data_dir, settings.synthetic_dir, settings.external_dir, settings.models_dir):
    _d.mkdir(parents=True, exist_ok=True)
