"""Re-upsert the synthetic ground-truth events from data/synthetic/events_truth.json.

Needed after any ingestion run made with the pre-26-Sep `_clear_document` (which deleted truth
rows per document). Idempotent: event_ids are stable, upsert merges.

    .venv\\Scripts\\python.exe scripts\\restore_truth.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nwis import db  # noqa: E402
from nwis import schema as S  # noqa: E402


def main() -> None:
    truth_path = ROOT / "data" / "synthetic" / "events_truth.json"
    rows = json.loads(truth_path.read_text(encoding="utf-8"))
    events = [S.DrillingEvent(**r) for r in rows]
    before = len(db.events_for())
    n = db.upsert_events(events)
    after = {r[0]: r[1] for r in db.session().exec(
        db.text("SELECT extraction_method, COUNT(*) FROM events GROUP BY 1")).all()}
    print(f"upserted {n} truth events; events before={before}; by method now={after}")


if __name__ == "__main__":
    main()
