# NWIS · 6-minute judge walkthrough

> Verified against the live `data/nwis.sqlite` (30 synthetic Upper Assam wells, fully ingested, extracted events present) and the running API. Numbers marked **(live)** can shift slightly with re-ingestion: re-check them in a dry run before presenting.
> Everything on screen is **illustrative synthetic data**; the header badge says so. Say it once, at the start.

## Pre-flight (T−15 min)

1. **Ingestion finished and Ollama idle** so Memory answers and alert recommendations come back in seconds (they no longer block the replay, but they do queue behind any running ingestion).
2. Start the single-process demo from the repo root in a fresh PowerShell:
   ```
   powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
   ```
   It builds `web/`, starts uvicorn on :8000 and opens `http://127.0.0.1:8000/office`. Use `-SkipBuild` if `web\dist` is current. Stop with `.\stop_all.ps1`.
3. **Demo cache.** `models/demo_qa_cache.json` holds "Girujan stuck pipe remedy" (cited, 4 sources) and "Tipam losses LCM" (an honest refusal). Re-warm after ingestion: `.venv\Scripts\python.exe -m nwis.search.demo_cache --warm`.
4. **Dry-run the replay once** (step 5 below) so models and caches are warm, then press **Stop** on /rig.
5. Browser at 100 % zoom, 1440×900 or larger; close other tabs. Map tiles need internet (OSM). Offline, the map keeps working on a blank dark background, or point `VITE_TILE_URL` at a local tile server and rebuild.
6. **Theme + language.** Top-right of the nav: **EN | हि** switches all UI chrome to Hindi (data, well/formation names and the example questions stay English), the sun/moon button switches dark ↔ light. Both choices stick per browser (localStorage), so set them once before judges arrive: dark + EN is the rehearsed default; use light on a washed-out projector. In Hindi the chatbot's greeting/off-topic line leads in Hindi.

**Extraction number for the deck (current shipped prompt):** P 0.91 / R 0.95 / F1 0.93 incident-level on 236 synthetic PDFs (30 % scanned), formation accuracy 1.00, depth MAE 0.4 m. Volve real data: quote from the hand-labelled result, not the synthetic number (`models/VOLVE_EVAL_NOTES.md`).

## Script

| t | Screen | Do | Say (one line each) |
|---|---|---|---|
| 0:00 | **/office**, well DUL-005, radius 5 km (URL `/office?well=DUL-005&r=5`) | Point at the badge, then the map | "Duliajan office view. One active well, a 5 km ring, and every offset well flagged by what went wrong there." |
| 0:30 | Map + **Ranked offsets** | Hover DUL-003 in the table (map ring highlights); point at the 5 bars | "Ranked by similarity, not distance. DUL-012 (3.4 km) scores 72; DUL-003 is closer at 2.2 km but deviated, so it scores 66. Five dimensions: distance, formation overlap, trajectory, target depth, difficulty. A dimension we can't compute shows *n/a*; it is never zero-filled." |
| 1:00 | **Correlation** tab (default *Formation-aligned*) | Toggle to *True vertical depth* and back | "Same wells, two depth references. Aligned on formation top, the Girujan stuck-pipe markers line up across wells; in raw TVD they scatter. Hover any marker for its report page, cause and remedy." |
| 1:30 | Dip readout (Girujan) | Point at it | "Structural dip fitted from all 30 wells: 0.12° toward 141°. It's a plane fit with azimuth, and it's what projects offset events onto our well." **(live: 62.6 m RMSE; say it if asked)** |
| 1:50 | **Memory** tab | Click the *Girujan stuck pipe remedy* chip (Demo cache on) | "Ask the archive. Every line of the answer carries a citation to a report page." |
| 2:10 | Answer card | Click the first chip `[DUL-012 \| WCR p3]` | "One click to the page it came from. The evidence is highlighted: pipe stuck while tripping, pipe-lax pill, freed after 6.6 h." Close with Esc. |
| 2:30 | (optional, 10 s) | Click *Tipam losses LCM* | "When evidence is thin, it refuses instead of guessing." |
| 2:40 | **Risk ahead of bit**, URL `…&tab=risk&md=430` (or drag the bit to 430 m) | Point at 500–550 m | "Bit at 430 m. The next 200 m, in 50 m bins. 500–550 m Girujan: stuck pipe, score 31, mostly offset precedent (DUL-003, 2.1 h NPT). Grey stack = precedent, live anomaly, model. The badge says **INDICATOR**: too few labelled events for a calibrated probability, so we don't claim one." |
| 3:20 | **/rig** (top nav: *Rig · live*) | Well DUL-005, start MD **430**, speed **500×**, **Start** | "Rig-site view, built for VSAT: alerts first, one poll a second, tiny payloads. We replay DUL-005's drilling log as if it were the eRTMAC feed." |
| 3:40 | Header + strip | Point at MD / formation / sparklines | "Bit depth, formation (Girujan), ROP, and the last 60 samples of torque, pit volume, gas, SPP." If the amber *Link degraded* bar appears: "That's the offline tolerance: last good data stays on screen." |
| 4:00 | **Lookahead advisory** card (Stuck pipe, rule *Lookahead advisory*) | Read the message | "Before the bit gets there: offset wells DUL-002, DUL-003 and DUL-011 had stuck pipe in the next 30–200 m of this interval, dip-corrected onto our well. Parameter anomalies fire separately and are N-of-M gated, so no alert storm." |
| 4:30 | **Investigate historical solutions** | Click; select DUL-011 in the left list | "The engineer's question is 'what did they do?'. DUL-011, 501 m, 16.1 h NPT: worked pipe with max overpull and spotted a 71 bbl pipe-lax pill. That's the actual DDR page, highlighted." |
| 5:00 | Back on the card | **Ack** | "Acknowledged. It drops below the live alerts and stays visible. ISA-18.2 style: ack, dedupe, cooldown, hard cap." |
| 5:15 | **/office → Review queue** | Show counters; switch *All extracted* if *Needs review* is empty; **Approve** one row | "Everything the pipeline read from PDFs lands here with a confidence score. Below 0.60 it's amber and waits for an engineer. Approvals are the labels that retrain extraction and risk: the feedback loop." **(live: 198 extracted incidents, typically 0 pending if the ingestion run put nothing below 0.60 — flip to *All extracted* to demo Approve)** |
| 5:45 | Top bar | Flip *Ground truth (synthetic, for judges)* on and back off | "By default you only see what we *extracted*. This switch shows the generator's ground truth, so you can check us." |
| 6:00 | End | | |

## Verification record (how each step was checked)

- `GET /map/data?well_id=DUL-005&radius_km=5` → 30 wells, 6 candidates: DUL-012 0.72, DUL-003 0.66 (2.2 km WSW), DUL-006 0.66, DUL-007 0.62, DUL-002 0.60, DUL-011 0.47. Difficulty dimension is available for all (logs present).
- `GET /dip/Girujan` → θ 0.12°, azimuth 141°, RMSE 62.6 m, n = 30.
- `GET /correlation/figure?well_ids=DUL-005,DUL-012,DUL-003,DUL-006,DUL-007&mode=tvd` → 60 extracted events, formation bands present.
- Memory: the cached answer for "Girujan stuck pipe remedy" is not refused, not degraded, 4 citations (DUL-012 WCR p3 6.6 h, DUL-003 2.1 h, DUL-004 4.0 h, DUL-005 2.7 h). Citation chips resolve through `/documents/resolve` → `/documents/{id}/text`.
- `GET /risk?well_id=DUL-005&md=430` → 500–550 m Girujan stuck_pipe score 0.307, precedent 0.5, reason `precedent:DUL-003:weight=0.52:hours_lost=2.1`, mode indicator.
- Headless: `python -m nwis.live.simulate --well DUL-005 --speed 500 --headless` → 11 alerts (7 precedent_zone, 4 param_anomaly), incl. *stuck_pipe @ 400 m: "+77–158 m ahead: 2 offset wells (DUL-003, DUL-011)"*.
- Through the UI (Playwright, `POST /ui/live/start {DUL-005, 500, start_md 430}`): *Lookahead advisory · Stuck pipe* appeared; the Investigate drawer listed DUL-003 (563 m, 2.1 h) and DUL-011 (501 m, 16.1 h, pipe-lax pill) with the DDR text highlighted; Ack worked. First lookahead advisory fires **2 s** after Start; its cited recommendation fills in ~18 s with Ollama idle (~50 s if something else is using the LLM) and reads as a 2-sentence driller instruction with a [well | page] citation.
- Playwright smoke (`web/e2e/smoke.spec.ts`) green on both :5173 (dev) and :8000 (built).

## Known limitations

- Replay does not freeze while an alert's recommendation is computed: the recommendation runs on a background thread, the alert card appears at once with "Recommendation pending…" and fills in on the next poll. Run the demo with Ollama idle for the snappiest answers.
- OSM map tiles need internet; set `VITE_TILE_URL` to a local tile server for a fully offline demo.
- `kick` stays in INDICATOR mode by design (too few labelled events for a calibrated model — see `models/PREDICT_METRICS.md`).
- Volve real-data numbers are precision/recall against a hand-labelled set, not the synthetic benchmark — quote them separately (see `models/VOLVE_EVAL_NOTES.md`), and re-check any number marked **(live)** in a dry run before presenting, since ingestion order can shift extracted-event counts slightly.
