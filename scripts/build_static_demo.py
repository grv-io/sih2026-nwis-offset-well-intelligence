"""Snapshot the NWIS API into JSON files for the static (GitHub Pages) demo.

The static build of the web app (``VITE_STATIC_DEMO=1``, see web/src/lib/static/)
has no server: every GET it would send to the API is answered from a file under
``web/public/demo-data/`` written by this script. The responses come from this
repo's own FastAPI app through ``fastapi.testclient.TestClient`` (no network, no
running server), so they are exactly what the live system returns.

What gets recorded
    basics   /health, /wells, /ui/summary, /risk/metrics, /review-queue,
             /dip/{formation} for every formation, /events (both basins, every
             source; the client filters), the chunk table (keyword search) and
             /documents/{id}/text for every page of every document
    map      /map/data for every Assam well x radius 1-10 km (0.5 km steps, the
             slider's grid) and the Volve basin; stored as one well list per
             (basin, source) plus {candidates, ring} per (well, radius), because
             the ranking does not depend on the event source
    corr     /correlation/figure for each well's default panel (active + top-4
             offsets at every radius) in both depth modes and both event sources,
             plus every single add/remove toggle of the DUL-005 panels
    risk     /risk for every Assam well at every 10 m of bit depth from 0 to TD
             (the Risk panel's slider grid) plus the panel's default depth
    qa       /search and /answer (LLM live, cache bypassed) for the rehearsed
             questions; needs Ollama running
    rig      full replay timelines (samples + every alert add/update/remove with
             its t_s, and the final cited recommendation) for the demo wells from
             several start depths; recommendations need Ollama running

Output is deterministic (sorted keys, fixed float rounding for coordinates and
samples) and is checked for absolute paths and secrets before it is written.

    python scripts/build_static_demo.py                 # everything
    python scripts/build_static_demo.py --only map,corr # selected sections
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("NWIS_LLM_TIMEOUT_S", "180")

OUT = ROOT / "web" / "public" / "demo-data"
SECTIONS = ("basics", "map", "corr", "risk", "qa", "rig")

RADII = [1 + 0.5 * i for i in range(19)]  # office radius slider: min 1, max 10, step 0.5
RISK_STEP_M = 10  # office risk slider step
CORR_TOP = 4  # CorrelationPanel default: active + top-4 candidates
CORR_CHIPS = 8  # CorrelationPanel shows up to 8 candidate chips
CORR_MAX_WELLS = 6
TOGGLE_WELLS = ("DUL-005",)  # the demo well: every single add/remove toggle is recorded

# Rig replays: demo wells whose logs produce lookahead advisories
# (checked with `python -m nwis.live.simulate --well <id> --speed 500 --headless`),
# each recorded from these start depths (0 = from the top of the log).
RIG_WELLS = ("DUL-005", "DUL-011", "DUL-012")
RIG_STARTS = (0, 430, 1000, 1500, 2000, 2500)
RIG_SPEED = 500.0
SPARK_COLS = ("t_s", "depth_md_m", "torque_kftlb", "pit_vol_bbl", "gas_pct", "rop_m_hr",
              "spp_psi", "flow_in_gpm", "flow_out_gpm", "wob_klbf", "mw_ppg")

_FORBIDDEN = [
    re.compile(r"[A-Za-z]:(\\\\|/)(Users|Documents and Settings)", re.IGNORECASE),  # Windows user-profile paths
    re.compile(r"/(Users|home)/[A-Za-z]"),
    re.compile(r"\b(hf|gho|ghp|github_pat)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"),
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_client = None


def client():
    global _client
    if _client is None:
        from fastapi.testclient import TestClient

        from api.main import app

        _client = TestClient(app)
    return _client


def get(path: str, **params) -> Any:
    r = client().get(path, params={k: v for k, v in params.items() if v is not None})
    if r.status_code != 200:
        raise RuntimeError(f"GET {path} {params} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def post(path: str, body: dict) -> Any:
    r = client().post(path, json=body)
    if r.status_code != 200:
        raise RuntimeError(f"POST {path} {body} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write(rel: str, obj: Any) -> int:
    s = dumps(obj)
    for pat in _FORBIDDEN:
        m = pat.search(s)
        if m:
            raise RuntimeError(f"{rel}: refusing to write, matched {pat.pattern!r} near {s[max(0, m.start() - 40):m.end() + 40]!r}")
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", newline="\n")
    return len(s.encode("utf-8"))


def rkey(r: float) -> str:
    return f"{r:g}"


def rnd(v: Any, nd: int) -> Any:
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, nd)
    if isinstance(v, list):
        return [rnd(x, nd) for x in v]
    if isinstance(v, dict):
        return {k: rnd(x, nd) for k, x in v.items()}
    return v


def slug(s: str) -> str:
    """ASCII file stem for a question (hash suffix keeps non-Latin questions unique)."""
    import hashlib

    base = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "q"
    return f"{base}-{hashlib.sha1(s.encode('utf-8')).hexdigest()[:8]}"


def norm_question(s: str) -> str:
    """Must match normQuestion() in web/src/lib/static/demo.ts."""
    s = re.sub(r"\s+", " ", s.strip().lower())
    return re.sub(r"[\s?.!।]+$", "", s)


def assam_wells() -> list[dict]:
    return get("/wells")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# basics
# --------------------------------------------------------------------------- #
def build_basics() -> None:
    from nwis import db
    from nwis.schema import FORMATIONS

    health = get("/health")
    write("health.json", health)
    write("wells.json", assam_wells())
    write("summary.json", get("/ui/summary"))
    write("risk-metrics.json", get("/risk/metrics"))
    write("review-queue.json", get("/review-queue"))
    for f in FORMATIONS:
        write(f"dip/{f}.json", get(f"/dip/{f}"))
    write("events/assam.json", get("/events", source="all"))
    write("events/volve.json", get("/events", source="all", basin="volve"))

    chunks = sorted(db.all_chunks(), key=lambda c: c.chunk_id)
    write("chunks.json", [{"chunk_id": c.chunk_id, "well_id": c.well_id, "page_ref": c.page_ref,
                           "document_id": c.document_id, "text": c.text} for c in chunks])

    docs = sorted(db.documents_for(), key=lambda d: d.document_id)
    index = []
    for d in docs:
        pages = {}
        meta = None
        for p in range(1, max(1, d.n_pages) + 1):
            payload = get(f"/documents/{d.document_id}/text", page=p)
            pages[str(p)] = {"text": payload["text"], "text_source": payload["text_source"]}
            meta = payload
        assert meta is not None
        name = meta["file_name"]
        write(f"docs/{d.document_id}.json", {
            "document_id": meta["document_id"], "well_id": meta["well_id"], "doc_type": meta["doc_type"],
            "report_date": meta["report_date"], "file_name": name, "path": name,  # never a local path
            "is_scanned": meta["is_scanned"], "n_pages": meta["n_pages"], "pages": pages,
        })
        index.append({"document_id": d.document_id, "well_id": d.well_id, "file_name": name})
    write("docs/index.json", index)
    log(f"basics: {len(docs)} documents, {len(chunks)} chunks, llm={health['deployment']['llm']}")


# --------------------------------------------------------------------------- #
# map
# --------------------------------------------------------------------------- #
def build_map() -> None:
    wells = assam_wells()
    for source in ("extracted", "truth"):
        full = get("/map/data", source=source)
        write(f"map/assam/wells_{source}.json", {"wells": full["wells"]})
        full = get("/map/data", source=source, basin="volve")
        write(f"map/volve/wells_{source}.json", {"wells": full["wells"], "note": full.get("note")})

    for w in wells:
        wid = w["well_id"]
        for r in RADII:
            d = get("/map/data", well_id=wid, radius_km=r, source="extracted")
            write(f"map/assam/{wid}_r{rkey(r)}.json", {"candidates": rnd(d["candidates"], 6), "ring": rnd(d["ring"], 6)})
    # the offset ranking must not depend on the event source (it is stored once)
    for wid, r in (("DUL-005", 5.0), ("MOR-003", 7.5)):
        a = get("/map/data", well_id=wid, radius_km=r, source="extracted")
        b = get("/map/data", well_id=wid, radius_km=r, source="truth")
        assert a["candidates"] == b["candidates"] and a["ring"] == b["ring"], f"candidates depend on source for {wid}"

    volve = get("/map/data", source="extracted", basin="volve")["wells"]
    for w in volve:
        wid = w["well_id"]
        for r in RADII:
            d = get("/map/data", well_id=wid, radius_km=r, source="extracted", basin="volve")
            write(f"map/volve/{wid}_r{rkey(r)}.json", {"candidates": d["candidates"], "ring": rnd(d["ring"], 6)})
    log(f"map: {len(wells)} Assam + {len(volve)} Volve wells x {len(RADII)} radii")


# --------------------------------------------------------------------------- #
# correlation
# --------------------------------------------------------------------------- #
def corr_key(ids: list[str]) -> str:
    """Must match corrKey() in web/src/lib/static/demo.ts: active well first, rest sorted."""
    return "_".join([ids[0]] + sorted(ids[1:]))


def correlation_sets() -> dict[str, list[str]]:
    sets: dict[str, list[str]] = {}
    for w in assam_wells():
        wid = w["well_id"]
        for r in RADII:
            cands = [c["well_id"] for c in get("/map/data", well_id=wid, radius_km=r, source="extracted")["candidates"]]
            default = [wid] + cands[:CORR_TOP]
            sets.setdefault(corr_key(default), default)
            if wid in TOGGLE_WELLS:
                for c in cands[:CORR_CHIPS]:
                    ids = [x for x in default if x != c] if c in default else default + [c]
                    if 1 <= len(ids) <= CORR_MAX_WELLS:
                        sets.setdefault(corr_key(ids), ids)
    return sets


def build_corr() -> None:
    sets = correlation_sets()
    total = 0
    for key, ids in sorted(sets.items()):
        for mode in ("normalised", "tvd"):
            for source in ("extracted", "truth"):
                d = get("/correlation/figure", well_ids=",".join(ids), mode=mode, source=source)
                d["figure"]["layout"].pop("template", None)  # the UI re-skins the figure and drops it anyway
                total += write(f"corr/{mode}_{source}_{key}.json", d)
    write("corr/index.json", sorted(sets))
    log(f"corr: {len(sets)} well sets x 2 modes x 2 sources, {total / 1e6:.1f} MB")


# --------------------------------------------------------------------------- #
# risk
# --------------------------------------------------------------------------- #
def _risk_for_well(args: tuple[str, float]) -> tuple[str, dict]:
    wid, td = args
    mds = set(range(0, int(round(td)) + 1, RISK_STEP_M))
    mds.add(int(min(2400, round(td * 0.7))))  # RiskPanel's default bit depth when the URL has none
    out = {}
    for md in sorted(mds):
        out[str(md)] = get("/risk", well_id=wid, md=md)
    return wid, out


def build_risk(workers: int) -> None:
    wells = [(w["well_id"], w["td_md_m"]) for w in assam_wells()]
    total = 0
    with ProcessPoolExecutor(workers) as ex:
        for wid, by_md in ex.map(_risk_for_well, wells):
            total += write(f"risk/{wid}.json", {"step_m": RISK_STEP_M, "by_md": by_md})
            log(f"risk: {wid} {len(by_md)} depths")
    log(f"risk: {len(wells)} wells, {total / 1e6:.1f} MB")


# --------------------------------------------------------------------------- #
# memory Q&A
# --------------------------------------------------------------------------- #
def build_qa() -> None:
    from nwis import llm
    from nwis.search.answer import EXAMPLE_QUESTIONS, HINDI_EXAMPLE, OFFLINE_PREFIX
    from nwis.search.demo_cache import DEFAULT_QUESTIONS

    if not llm.available():
        raise SystemExit("qa: the LLM server is not reachable; start Ollama first (answers must be live, not cached)")
    questions = list(dict.fromkeys(DEFAULT_QUESTIONS + EXAMPLE_QUESTIONS + [HINDI_EXAMPLE]))
    index = []
    for q in questions:
        t0 = time.time()
        hits = get("/search", q=q, k=8)
        ans = post("/answer", {"query": q, "use_cache": False, "lang": "en"})
        if ans["text"].startswith(OFFLINE_PREFIX):
            raise SystemExit(f"qa: {q!r} came back from the offline path; is Ollama busy?")
        f = f"qa/{slug(q)}.json"
        write(f, {"question": q, "search": hits, "answer": ans})
        index.append({"question": q, "norm": norm_question(q), "file": f})
        log(f"qa: {q!r} refused={ans['refused']} degraded={ans['degraded']} cites={len(ans['citations'])} {time.time() - t0:.0f}s")
    write("qa/index.json", index)


# --------------------------------------------------------------------------- #
# rig replays
# --------------------------------------------------------------------------- #
def _record_run(args: tuple[str, float]) -> dict:
    """Replay `well_id` from `start_md` through nwis.live.replay.ReplaySession exactly as
    the API does (seek, then consume every sample in order), recording each change to
    the open-alert set with the sample time it happened at. The recommendation thread
    is replaced by a recorder; recommendations are computed afterwards, one at a time,
    from the same alert copy and precedent events the thread would have used."""
    well_id, start_md = args
    from nwis.live import alerts as A
    from nwis.live.replay import ReplaySession

    A._id_counter = itertools.count(1)  # deterministic ALERT-000001...
    pending: dict[str, tuple[dict, list]] = {}

    def record_recommend(self, alert, precedent_events):
        if alert.recommendation is None:
            alert.recommendation = "Recommendation pending..."
        if not alert.citations:
            alert.citations = [f"{alert.well_id} live sensors @ t={alert.t_s:.0f}s"]
        pending[alert.alert_id] = (alert.model_dump(mode="json"), [dict(e) if isinstance(e, dict) else e for e in precedent_events])

    A.AlertEngine._try_recommend = record_recommend
    sess = ReplaySession(well_id=well_id, speed=RIG_SPEED)
    if start_md > 0:
        sess.seek(start_md)
    start_idx = int(sess._idx)
    events: list[dict] = []
    seen: dict[str, tuple] = {}
    for i in range(start_idx, len(sess.df)):
        sess._consume(sess.df.iloc[i])
        t = float(sess.df["t_s"].iloc[i])
        cur = dict(sess.alert_engine.open)
        for aid, a in cur.items():
            state = (a.severity.value, tuple(a.citations))
            if aid not in seen:
                events.append({"t_s": t, "type": "add", "alert": a.model_dump(mode="json")})
            elif seen[aid] != state:
                events.append({"t_s": t, "type": "update", "alert_id": aid,
                               "severity": a.severity.value, "citations": list(a.citations)})
            seen[aid] = state
        for aid in [x for x in seen if x not in cur]:
            events.append({"t_s": t, "type": "remove", "alert_id": aid})
            del seen[aid]
    return {"well_id": well_id, "start_md": start_md, "start_idx": start_idx,
            "signals_unavailable": sorted(sess.alert_engine.signals_unavailable),
            "events": events, "pending": pending}


def _samples(well_id: str) -> dict:
    from nwis.live.replay import _lookup_formation, load_log

    df, _ = load_log(well_id)
    cols = {c: [rnd(float(v), 3) if v is not None else None for v in df[c].tolist()] for c in SPARK_COLS if c in df.columns}
    formations: list[list] = []
    last = None
    for i, md in enumerate(df["depth_md_m"].tolist()):
        f = _lookup_formation(well_id, float(md)) or last
        if f != last:
            formations.append([i, f])
            last = f
    return {"well_id": well_id, "n": int(len(df)), "cols": cols, "formations": formations}


def build_rig(workers: int) -> None:
    from nwis import llm
    from nwis.live import recommend as reco
    from nwis.schema import Alert

    if not llm.available():
        raise SystemExit("rig: the LLM server is not reachable; start Ollama first (recommendations must be live)")
    for wid in RIG_WELLS:
        s = _samples(wid)
        write(f"rig/{wid}/samples.json", s)
        log(f"rig: {wid} samples {s['n']}")

    jobs = [(w, float(m)) for w in RIG_WELLS for m in RIG_STARTS]
    runs: list[dict] = []
    with ProcessPoolExecutor(workers) as ex:
        for run in ex.map(_record_run, jobs):
            runs.append(run)
            log(f"rig: {run['well_id']} from {run['start_md']:.0f} m: "
                f"{sum(1 for e in run['events'] if e['type'] == 'add')} alerts")

    index: dict[str, dict] = {w: {"starts": []} for w in RIG_WELLS}
    for run in runs:
        recs = {}
        for aid, (alert_d, precedents) in sorted(run.pop("pending").items()):
            t0 = time.time()
            work = Alert(**alert_d)
            reco.recommend_for_alert(work, precedent_events=precedents)
            recs[aid] = {"recommendation": work.recommendation, "citations": list(work.citations)}
            log(f"rig: {run['well_id']}@{run['start_md']:.0f} {aid} {alert_d['rule']}/{alert_d['hazard']} "
                f"recommendation in {time.time() - t0:.0f}s")
        run["recommendations"] = recs
        md = int(run["start_md"])
        write(f"rig/{run['well_id']}/run_{md}.json", run)
        index[run["well_id"]]["starts"].append(md)
    for v in index.values():
        v["starts"].sort()
    write("rig/index.json", {"speed_recorded": RIG_SPEED, "wells": index})


# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Snapshot the API for the static GitHub Pages demo.")
    ap.add_argument("--only", default=",".join(SECTIONS), help=f"comma-separated sections ({', '.join(SECTIONS)})")
    ap.add_argument("--workers", type=int, default=3, help="processes for the risk and rig sections")
    ap.add_argument("--clean", action="store_true", help="delete web/public/demo-data first")
    args = ap.parse_args(argv)
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    bad = [s for s in only if s not in SECTIONS]
    if bad:
        raise SystemExit(f"unknown section(s): {bad}")
    if args.clean and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for s in only:
        log(f"== {s}")
        if s in ("risk", "rig"):
            globals()[f"build_{s}"](args.workers)
        else:
            globals()[f"build_{s}"]()
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    n = sum(1 for p in OUT.rglob("*") if p.is_file())
    log(f"done in {time.time() - t0:.0f}s: {n} files, {size / 1e6:.1f} MB in {OUT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
