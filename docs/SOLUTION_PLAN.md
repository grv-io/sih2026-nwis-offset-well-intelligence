# SIH26121 — NWIS · Solution Plan

> This file is the design decision record for the project, not a literature survey.

## 0. One-line pitch

**NWIS = eRTMAC ka institutional memory.** Purane WCR/DDR PDFs ko structured drilling-event ledger me badlo, offset wells ko map + formation-depth panel pe rakho, aur live bit ke aage 50 m ka "risk-ahead-of-bit" score nikaal ke Duliajan aur rig dono ko alert do, har alert ke saath offset-well ka source page.

## 1. What we build (maps 1:1 to PS bullets i–vii)

| PS | Module | What the judge sees | Core tech |
|---|---|---|---|
| i | **Ingest** | Ek messy scanned DDR upload → 10 s me structured event rows, low-confidence rows yellow (human review queue) | OCR (Tesseract/RapidOCR fallback for scanned pages) → text extraction → local Ollama qwen2.5 + instructor/Pydantic → schema validation, fully local, no external API |
| ii | **Map** | Assam map, active well, radius slider, offsets ranked by *similarity* (distance + formation match + trajectory type), not just distance | Radius filter + multi-dimension similarity scoring; Leaflet map; minimum-curvature well trajectories |
| iii | **Memory** | Search box: "Girujan me stuck pipe kaise chhudaya?" → cited answers with DDR page thumbnails | SQLite FTS5 + nomic-embed-text embeddings, hybrid (RRF) search, answers with citations |
| iv | **Correlate** | Side-by-side depth panel, formation bands, event markers; wells aligned on **formation + offset-from-top**, not raw MD | Formation tops + TVD conversion; Plotly panel |
| v | **Predict** | Risk-ahead-of-bit gauge per 50 m interval, SHAP top-3 reasons | XGBoost per hazard (stuck pipe, mud loss, kick, overpressure) on offset-event density + live params, transparent weighted fusion |
| vi | **Alert** | Replay of a live well; alert fires 50 m above a flagged zone with "Well X, 2019, same formation: did Y" | Replay loop standing in for eRTMAC (WITSML 1.4 poll / ETP push in production); ISA-18.2 style N-of-M + dwell + cooldown |
| vii | **Dashboard** | Office view (map + panel + search) and Field view (alert-first, low-bandwidth) | React + Vite + TypeScript, Leaflet + Plotly, EN/HI toggle, light/dark themes |

## 2. Data plan (no OIL data given)

| Purpose | Source | Status |
|---|---|---|
| Real DDR/WCR text + WITSML streams + trajectories | **Equinor Volve** (24 wells, open licence; 1,759 DDRs) | Public, verified; parsed-DDR repo exists |
| Wellbore-history narrative + formation tops + coords | **Sodir FactPages** CSV | Public, free, no login |
| Labelled formation tops for correlation | **FORCE 2020** (98 wells, NLOD licence) | Public |
| OCR robustness (different scan style) | Texas RRC / WA WAPIMS scanned well files | Public |
| Assam-flavoured demo | **Synthetic Upper Assam wells** generated from OIL SPG-2013 + USGS 2208-D stratigraphy table | We build it; label "illustrative" |
| Real Indian data | **DGH NDR** (institutional email + HOD letter, ~24 h) | Access path scoped, NOT claimed working |

Deck framing: "Validated on the same public benchmark (Volve) that IPTC-2025 and arXiv-2026 TADI use; recalibrates on OIL's own archive post-deployment via the feedback loop."

## 3. Differentiators (say these, in this order)

1. **Formation-indexed lookahead alerting** (bullets iv + vi together): most teams stop at a real-time dashboard.
2. **Risk-ahead-of-bit per-depth fusion**: no peer-reviewed precedent found; framed as our contribution, scoped conservatively.
3. **Similarity-ranked offsets** (distance + formation + trajectory), not radius alone.
4. **Every AI output carries its source page** (DDR line / WCR page / offset well id).
5. **Feedback loop**: human corrections retrain local extraction + risk models (DrillScribe finding: NPT models don't transfer across operators).
6. **Two personas**: rig (VSAT, alert-first) and Duliajan (full analytics).
7. **Upper Assam-native demo**: Girujan stuck pipe, Tipam losses, Barail overpressure on the map.

## 4. What we will NOT claim

- Any OIL NPT %, eRTMAC vendor, or WCR template (not public).
- ML accuracy > what we measured; no "80% NPT reduction" vendor numbers.
- DGH NDR / DISKOS access as working.
- A full knowledge graph over decades of OIL docs (multi-year job); we show the pipeline at small scale.

## 5. Three honest risks + mitigations (slide 4)

| Risk | Mitigation |
|---|---|
| Legacy scan quality (photocopies, stamps, handwriting) | Hybrid OCR + confidence flag + human-review queue; per-template extraction profiles |
| DDR/WCR format drift across decades and fields | Configurable profiles, not one parser; feedback loop recalibrates |
| No live eRTMAC access / cold-start in a new area | WITSML replay adapter documented; cold-start falls back to basin-level formation priors |

## 6. Impact framing (slide 5, one number with working shown)

Illustrative, clearly labelled: "manual offset review of N reports takes ~X engineer-hours per well (assumption); NWIS returns the same in seconds with citations." Baker Hughes publicly claims 5 days → 5 hours for offset analysis; cite as industry evidence, not as our result. If time permits, show one real Volve chart: extracted event density by formation vs. actual NPT hours.

## 7. Build scope

**MVP delivered:** synthetic + Volve PDFs ingested into a per-well extraction table; Leaflet radius map with similarity-ranked offsets; SQLite FTS5 + embedding search over drilling-event text; Plotly correlation panel across multiple wells; XGBoost per hazard with SHAP explanations; WebSocket-style replay with rule-based alerts (lookahead, N-of-M, cooldown); a React office/rig dashboard. Runs laptop-only, no GPU required (qwen2.5:7b via Ollama).

**Not built / stretch:** all five hazards at model-backed confidence (two remain indicator-only for lack of labelled events); a live WITSML/ETP adapter to a real eRTMAC feed (replay stands in for it); active-learning UI for the review queue; 3D trajectory views; offline map tiles.
