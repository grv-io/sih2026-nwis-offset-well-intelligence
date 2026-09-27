# NWIS — Nearby Wells Intelligence System

**eRTMAC-NWIS** is a standalone offset-well knowledge and decision-support platform that sits alongside Oil India's real-time drilling monitoring system (eRTMAC). It turns years of scanned/digital Well Completion Reports and Daily Drilling Reports into a structured, searchable event ledger, ranks nearby wells by geological and operational similarity rather than distance alone, correlates drilling parameters on a formation-aligned depth axis, scores drilling risk ahead of the bit from offset-well precedent, and pushes cited, lookahead alerts to both the Duliajan office and the rig site — all running fully locally, no cloud LLM required.

## Problem (SIH26121)

Oil India's eRTMAC gives real-time data from the *active* well, but drilling decisions in geologically complex formations also need insight from *nearby and historical* wells. That knowledge sits scattered across thousands of WCR/DDR PDFs and individual engineers' memory, so retrieval is slow and depends on who happens to remember what. The result: delayed decisions and missed chances to proactively mitigate risks like stuck pipe, mud losses, kicks, and overpressure zones before they recur in the same formation.

## What NWIS does

| # | PS ask | NWIS module | How |
|---|---|---|---|
| i | Extract & structure historical reports (AI/NLP/OCR) | **Ingest** | pdfplumber for digital text, RapidOCR (Tesseract fallback) for scanned pages → Ollama `qwen2.5:7b` (local, via `instructor`) extracts a 12-type drilling-event taxonomy with a quote check, a confidence score, and a human review queue for anything below threshold |
| ii | Map-based visualization of nearby wells | **Map** | Radius search around the active well; offsets ranked by a multi-dimension similarity score (distance, formation overlap, trajectory type, target depth, difficulty) — Leaflet |
| iii | Searchable knowledge repository | **Memory** | SQLite FTS5 + `nomic-embed-text` embeddings, hybrid search (Reciprocal Rank Fusion), cross-document incident linking, answers cite the exact report page; refuses instead of guessing when evidence is thin |
| iv | Correlate geological/drilling data by depth & formation | **Correlate** | Minimum-curvature MD→TVD conversion, formation-top alignment, azimuth/dip plane fit across offset wells; Plotly side-by-side panel |
| v | Predictive risk models from offset-well behaviour | **Predict** | XGBoost + SHAP per hazard (stuck pipe, mud loss, kick, overpressure), fused with offset precedent and live-parameter anomaly scores (weights 0.5 / 0.3 / 0.2); switches to an honest "indicator" mode instead of a fake probability when a hazard has too few labelled events |
| vi | Real-time alerts & recommendations | **Alert** | Live replay of drilling parameters with lookahead-of-bit precedent alerts, parameter-anomaly detection gated N-of-M + dwell + cooldown (ISA-18.2 style), and a cited, LLM-generated driller recommendation |
| vii | Dashboard for field & office | **Dashboard** | React + Vite + TypeScript; `/office` (map, correlation, memory, review queue) and `/rig` (alert-first, low-bandwidth); English/Hindi toggle; light/dark themes |

## Architecture

```
                         ┌─────────────────────────────────────────┐
                         │              SOURCES                     │
                         │  Scanned/digital WCR & DDR PDFs           │
                         │  Live drilling-parameter feed (replay)    │
                         └───────────────────┬───────────────────────┘
                                             │
                         ┌───────────────────▼───────────────────────┐
                         │        INGEST & STORE                     │
                         │  pdfplumber / RapidOCR  →  page chunks     │
                         │  Ollama qwen2.5:7b + instructor            │
                         │    → 12-type event taxonomy                │
                         │    → quote check + confidence + review     │
                         │  nomic-embed-text embeddings                │
                         │  SQLite + FTS5  (data/nwis.sqlite)          │
                         └───────────────────┬───────────────────────┘
                                             │
                         ┌───────────────────▼───────────────────────┐
                         │        FASTAPI SERVICES (api/)             │
                         │  /nearby      similarity ranking            │
                         │  /correlation TVD + azimuth/dip fit         │
                         │  /risk        XGBoost + SHAP fusion         │
                         │  /answer      hybrid RRF + cited answers    │
                         │  /live        replay + lookahead + alerts   │
                         └───────────────────┬───────────────────────┘
                                             │
                         ┌───────────────────▼───────────────────────┐
                         │        REACT SCREENS (web/)                │
                         │  /office  — map, correlation, memory,      │
                         │             review queue                   │
                         │  /rig     — alert-first live view          │
                         │  Leaflet + Plotly, EN/HI, light/dark        │
                         └─────────────────────────────────────────────┘
```

**Real components, named:** extraction is `pdfplumber` + `RapidOCR` → `Ollama qwen2.5:7b` via `instructor` (structured, validated Pydantic output) against a frozen 12-type event taxonomy, with a quote check against source text, a confidence score, and a human review queue for anything uncertain. Cross-document incident linking connects the same real-world event across DDR/WCR pairs. Storage is SQLite + FTS5 + `nomic-embed-text` — no external database required. `/nearby` ranks offset wells by similarity with honest degradation (a dimension that can't be computed shows `n/a`, never zero). `/correlation` uses minimum-curvature MD→TVD and an azimuth/dip plane fit across offset wells. `/risk` fuses XGBoost + SHAP with offset precedent and anomaly scores (0.5 / 0.3 / 0.2), switching between an "indicator" and a "supervised" mode depending on how many labelled events exist for that hazard. `/answer` is hybrid RRF search over the event ledger with cited, scope-guarded answers. `/live` replays a well's drilling log with lookahead-of-bit precedent alerts, N-of-M/cooldown-gated anomaly alerts, and cited recommendations. The frontend is React + Vite + TypeScript with Leaflet (map) and Plotly (correlation/sparklines), an `/office` and a `/rig` screen, English/Hindi toggle, and light/dark themes.

## Quick start

```powershell
# 1. Python environment
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Local models (Ollama must be running)
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# 3. Web build
cd web
npm install
npm run build
cd ..

# 4. Run the demo (single process, builds web if needed)
powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
# -> http://127.0.0.1:8000/office   (and /rig)
# stop with: powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
```

`data/nwis.sqlite` ships pre-ingested with the synthetic demo data, so the UI is usable immediately — no need to run the ~80-minute local ingestion first. To re-ingest from scratch: `python -m nwis.ingest.run data/synthetic/docs`.

## 6-minute demo walkthrough

1. **`/office`, well DUL-005, radius 5 km.** The map shows one active well and every offset well flagged by what went wrong there. Offsets are ranked by similarity, not distance — a farther well with a formation/trajectory match can outrank a closer but dissimilar one.
2. **Correlation tab.** Toggle between formation-aligned and true-vertical-depth views. On formation alignment, the same stuck-pipe markers line up across wells; in raw depth they scatter. Hover any marker for its report page, cause and remedy. A structural-dip readout shows the fitted plane (angle + azimuth) used to project offset events onto the active well.
3. **Memory tab.** Ask the archive a question ("Girujan stuck pipe remedy") and get a cited answer with a one-click link to the exact report page and the evidence highlighted. Ask something the archive doesn't support and it refuses instead of guessing.
4. **Risk ahead of bit.** A 50 m-binned risk gauge for the next few hundred metres of hole, with a grey stack showing how much of the score comes from offset precedent vs. live anomaly vs. the model, and an "indicator" badge whenever there isn't enough labelled data to claim a calibrated probability.
5. **`/rig`.** Start a replay at speed. A lookahead advisory fires *before* the bit reaches a flagged zone, naming the offset wells and what happened there, dip-corrected onto the active well. Parameter anomalies are gated N-of-M so a single sensor blip doesn't trigger an alert storm. "Investigate historical solutions" opens the actual offset-well report page. Acknowledge, and the alert drops below the live list (dedupe + cooldown, ISA-18.2 style).
6. **Review queue.** Everything the pipeline extracted lands here with a confidence score; below 0.60 it waits for an engineer. Approvals become the labels that retrain extraction and risk models — the feedback loop. A "ground truth" toggle (synthetic data only) lets a reviewer check the pipeline's extractions against the generator's known-correct answer.

Full script with exact API calls and expected responses: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

## Results, with caveats

| Metric | Value | Caveat |
|---|---|---|
| Extraction P / R / F1 (incident-level) | 0.91 / 0.95 / 0.93 | 236 synthetic PDFs, 30% scanned |
| Formation accuracy | 1.00 | Synthetic data |
| Depth MAE | 0.4 m | Synthetic data |
| XGBoost ROC-AUC (leave-wells-out) | 0.86 – 0.94 | Synthetic data; two hazards remain indicator-only (too few labelled events for a calibrated model) |
| Lookahead advisory latency | ~2 s | From replay start |
| Cited recommendation latency | ~18 s | Ollama idle; ~50 s if the LLM is busy with something else |
| Volve real data — precision / recall (overall) | 0.71 / 0.29 | 40 hand-labelled documents, one independent labeller |
| Volve real data — recall on drilling hazards (kick/mud loss) | 0.71 / 0.45-class-weighted | Same 40-document hand-labelled set; equipment/software NPT and casing-running tight-hole phrasing are the known recall gap |

The full methodology and honest read for each number lives in [`models/PREDICT_METRICS.md`](models/PREDICT_METRICS.md), [`models/VOLVE_EVAL_NOTES.md`](models/VOLVE_EVAL_NOTES.md), and [`data/external/volve/HAND_LABELS.md`](data/external/volve/HAND_LABELS.md).

## What we do not claim

- No Oil India NPT percentage, eRTMAC vendor detail, or real WCR/DDR template — none of that is public, so the demo uses synthetic Upper Assam data and public Volve (North Sea) data instead.
- No accuracy numbers beyond what is actually measured here; no vendor-style "80% NPT reduction" claims.
- No working DGH NDR / DISKOS data access — that path is scoped but not implemented.
- No production-grade WITSML/ETP connection to a live eRTMAC feed — the live pipeline is validated against a replay of recorded drilling logs, with the adapter point documented.
- No calibrated probability for hazards with too few labelled events (shown as "indicator", not "supervised", risk).
- The real-data (Volve) recall number is a known, stated gap, not a hidden one — see the caveats above.

## Data & licences

- **Synthetic Upper Assam field** (`data/synthetic/`): entirely synthetic, generated from public stratigraphy references — Mandal & Dasgupta (SPG, 2013) and USGS Bulletin 2208-D — for the demo. **Illustrative only; not real Oil India data.**
- **Equinor Volve Open Data** (`data/external/volve/`): real North Sea DDR text, used to validate extraction against genuine field-report language. Governed by the Equinor Open Data Licence (research/study/education use); only small methodology/reference files are committed here (raw parquet/CSV downloads are not).
- **Sodir (Norwegian Offshore Directorate) FactPages**: formation-tops reference data, under the Norwegian Licence for Open Government Data (NLOD).

## Repo map

```
nwis/            core Python package: ingest, geo, predict, search, live, synth, external/volve
api/             FastAPI app and routers (main, live, risk, search, ui)
web/             React + Vite + TypeScript frontend (office/rig screens)
tests/           pytest suite
scripts/         one-off maintenance scripts (e.g. restore_truth.py)
data/            synthetic demo data, nwis.sqlite (pre-ingested), Volve reference files
models/          trained XGBoost models, extraction/predict metrics, evaluation notes
docs/            problem statement text, solution plan, demo script
ppt/final/       idea-stage slide deck (.pptx / .pdf)
run_demo.ps1     single-process demo (build web once, serve UI + API on :8000)
run_all.ps1      dev mode (API with --reload + Vite dev server)
stop_all.ps1     stop everything the above started
```

## Team

Team ______ · MNIT Jaipur (Chemical Engineering + Software)

## Deck

Idea-stage presentation: [`ppt/final/SIH26121_Idea_Presentation.pdf`](ppt/final/SIH26121_Idea_Presentation.pdf) ([.pptx](ppt/final/SIH26121_Idea_Presentation.pptx))
