"""Orchestrates the full synthetic Upper Assam field: wells, tops, surveys, hazards, events,
DDR/WCR documents (text + PDF, some scanned/image-only), 1 m-step logs, and a SQLite load.

Usage:
    python -m nwis.synth.field --seed 42 --out data/synthetic

ILLUSTRATIVE / SYNTHETIC dataset. See the generated README.md for details and caveats.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from nwis import db
from nwis.config import settings
from nwis.schema import FormationTop, SurveyStation
from nwis.synth import events as events_mod
from nwis.synth import hazards
from nwis.synth import logs as logs_mod
from nwis.synth import reports as reports_mod
from nwis.synth import wells as wells_mod

SCANNED_WELL_FRACTION = 0.30


def _tops_for_well(sw: wells_mod.SynthWell) -> list[FormationTop]:
    order = [f for f, _ in sw.tops_tvd]
    tvd_by_f = dict(sw.tops_tvd)
    md_by_f = dict(sw.tops_md)
    out = []
    for i, f in enumerate(order):
        if i + 1 < len(order):
            base_md, base_tvd = md_by_f[order[i + 1]], tvd_by_f[order[i + 1]]
        else:
            base_md, base_tvd = float(sw.trajectory.md[-1]), float(sw.trajectory.tvd[-1])
        out.append(FormationTop(
            well_id=sw.well.well_id, formation=f,
            top_md_m=round(md_by_f[f], 1), top_tvd_m=round(tvd_by_f[f], 1),
            base_md_m=round(base_md, 1), base_tvd_m=round(base_tvd, 1),
        ))
    return out


def _surveys_for_well(sw: wells_mod.SynthWell) -> list[SurveyStation]:
    t = sw.trajectory
    return [SurveyStation(well_id=sw.well.well_id, md_m=round(float(m), 1),
                           inc_deg=round(float(i), 2), azi_deg=round(float(a), 2))
            for m, i, a in zip(t.md, t.inc_deg, t.azi_deg)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic Upper Assam field.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default="data/synthetic")
    parser.add_argument("--db-path", type=str, default=None,
                         help="Override the SQLite path (defaults to nwis.config.settings.db_path). "
                              "Useful for tests / avoiding a locked shared DB.")
    args = parser.parse_args(argv)
    db_path = Path(args.db_path) if args.db_path else None

    rng = np.random.default_rng(args.seed)
    out_root = Path(args.out)
    docs_root = out_root / "docs"
    logs_root = out_root / "logs"
    out_root.mkdir(parents=True, exist_ok=True)
    docs_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)

    print(f"[nwis.synth.field] seed={args.seed} out={out_root}")

    synth_wells = wells_mod.generate_wells(rng)
    well_ids = [sw.well.well_id for sw in synth_wells]
    n_scanned = max(1, round(len(well_ids) * SCANNED_WELL_FRACTION))
    scanned_ids = set(rng.choice(well_ids, size=n_scanned, replace=False).tolist())

    all_tops: list[FormationTop] = []
    all_surveys: list[SurveyStation] = []
    all_events = []
    all_documents = []

    total_ddr_pdfs = 0

    for sw in synth_wells:
        tops = _tops_for_well(sw)
        surveys = _surveys_for_well(sw)
        all_tops.extend(tops)
        all_surveys.extend(surveys)

        events = events_mod.generate_events(sw, rng)
        is_scanned = sw.well.well_id in scanned_ids
        documents, _schedule = reports_mod.generate_well_reports(sw, events, docs_root, is_scanned, rng)
        total_ddr_pdfs += sum(1 for d in documents if d.doc_type == "DDR")

        log_df = logs_mod.generate_log(sw, events, rng)
        log_df.to_parquet(logs_root / f"{sw.well.well_id}.parquet", index=False)

        all_events.extend(events)
        all_documents.extend(documents)

    # ---- CSV / JSON exports -------------------------------------------------
    wells_rows = [sw.well.model_dump(mode="json") for sw in synth_wells]
    tops_rows = [t.model_dump(mode="json") for t in all_tops]
    surveys_rows = [s.model_dump(mode="json") for s in all_surveys]
    events_rows = [e.model_dump(mode="json") for e in all_events]
    docs_rows = [d.model_dump(mode="json") for d in all_documents]

    pd.DataFrame(wells_rows).to_csv(out_root / "wells.csv", index=False)
    pd.DataFrame(tops_rows).to_csv(out_root / "tops.csv", index=False)
    pd.DataFrame(surveys_rows).to_csv(out_root / "surveys.csv", index=False)
    (out_root / "events_truth.json").write_text(json.dumps(events_rows, indent=2), encoding="utf-8")
    (out_root / "documents.json").write_text(json.dumps(docs_rows, indent=2), encoding="utf-8")

    hot_zones_json = [{**{k: v for k, v in hz.items() if k != "hazard"}, "hazard": hz["hazard"].value}
                       for hz in hazards.HOT_ZONES]
    (out_root / "hot_zones.json").write_text(json.dumps(hot_zones_json, indent=2), encoding="utf-8")

    readme = f"""# Synthetic Upper Assam field — ILLUSTRATIVE / SYNTHETIC

This dataset is **entirely synthetic**, generated by `nwis/synth/` (`python -m nwis.synth.field
--seed {args.seed}`) for the SIH26121 NWIS demo. It illustrates the shape of OIL-realistic
Upper Assam drilling data (wells, formation tops, surveys, DDR/WCR documents, drilling-parameter
logs, and ground-truth events) — it is **not** real OIL India data and must never be presented
as such. It is built from public Upper Assam stratigraphy references (Mandal & Dasgupta, SPG
2013; USGS Bulletin 2208-D), not from any OIL India well.

## Contents
- `wells.csv` — {len(synth_wells)} wells across 3 fields (Duliajan, Moran, Naharkatiya).
- `tops.csv` — formation tops per well, canonical order (Alluvium -> ... -> Basement).
- `surveys.csv` — directional survey stations (MD/inc/azi) every ~30 m, minimum-curvature.
- `events_truth.json` — {len(all_events)} ground-truth `DrillingEvent` records
  (`extraction_method=synthetic_truth`, `extraction_confidence=1.0`) — the label set for the
  extraction-accuracy evaluation.
- `documents.json` — {len(all_documents)} `SourceDocument` records (DDR + WCR), each with a
  `.txt` and `.pdf` under `docs/<well_id>/`.
- `hot_zones.json` — {len(hazards.HOT_ZONES)} spatially-clustered hazard zones (lat/lon/radius_km/
  formation/hazard) that make nearby-well hazard correlation real, not coincidental.
- `logs/<well_id>.parquet` — 1 m-step drilling-parameter log per well
  (`nwis.schema.LiveSample` columns) with formation-dependent baselines and precursor signals
  in the 30-80 m before each ground-truth event.

## Known compressions (documented, not accidental)
- **DDR cadence.** A real Upper Assam well drills over 60-150+ calendar days. To keep the PDF
  corpus at the requested ~150-250 total (generated: **{total_ddr_pdfs}** DDR PDFs + 30 WCRs),
  each well's campaign is represented as 6-10 "report-days" (`nwis/synth/reports.py:build_schedule`),
  spaced one calendar day apart, with per-report-day footage sized by a per-formation ROP
  baseline (slow in Barail/Kopili/Sylhet, fast in Alluvium/Tipam). DDR PDFs are only rendered for
  report-days with an event, +/-1 day around it, and every 5th report-day.
- **Scanned/no-text-layer PDFs.** ~30% of wells ({n_scanned}/{len(synth_wells)}:
  {sorted(scanned_ids)}) have ALL their DDR/WCR PDFs rendered as rotated (0.3-0.8 deg),
  gaussian-noised, mildly-blurred page images with no text layer (`is_scanned=True`) — for the
  OCR extraction path. The rest are born-digital text PDFs.
- **Structural model.** Formation tops = a fixed smooth spatial field (gentle NE dip + a
  basement-high shallowing near Dibrugarh-Tinsukia) + small per-well noise, not real seismic/
  well-log-derived structure.

## Regenerate
```
.venv\\Scripts\\python.exe -m nwis.synth.field --seed {args.seed} --out {args.out}
```
"""
    (out_root / "README.md").write_text(readme, encoding="utf-8")

    # ---- SQLite load ---------------------------------------------------------
    db.reset(db_path)
    n_wells = db.upsert_wells([sw.well for sw in synth_wells])
    n_tops = db.add_tops(all_tops)
    n_surveys = db.add_surveys(all_surveys)
    n_docs = db.upsert_documents(all_documents)
    n_events = db.upsert_events(all_events)

    # ---- Summary --------------------------------------------------------------
    by_field = pd.DataFrame(wells_rows)["field"].value_counts().to_dict()
    by_event_type = pd.DataFrame(events_rows)["event_type"].value_counts().to_dict()

    print("\n=== Synthetic Upper Assam field: summary ===")
    print(f"Wells:          {n_wells}  ({by_field})")
    print(f"Formation tops: {n_tops}")
    print(f"Survey stations:{n_surveys}")
    print(f"Documents:      {n_docs}  (DDR PDFs={total_ddr_pdfs}, WCRs={len(synth_wells)}, "
          f"scanned wells={n_scanned})")
    print(f"Truth events:   {n_events}  {by_event_type}")
    print(f"Hot zones:      {len(hazards.HOT_ZONES)}")
    print(f"Logs:           {len(synth_wells)} parquet files under {logs_root}")
    print(f"Output root:    {out_root.resolve()}")
    print(f"SQLite DB:      {db_path or settings.db_path}")


if __name__ == "__main__":
    main()
