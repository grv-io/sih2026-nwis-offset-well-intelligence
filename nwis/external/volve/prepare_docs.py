"""Convert Volve DDR parquet rows into per-well, per-report-date `.txt` documents in
the same narrative shape the synthetic DDRs use (compare
`data/synthetic/docs/DUL-001/DUL-001_DDR_2017-06-12.txt`) -- so the existing,
unmodified ingestion pipeline (`nwis/ingest/run.py` + `chunk.py` + `extract_events.py`)
can run over real Volve text without any code change.

    python -m nwis.external.volve.prepare_docs [--top-n 5] [--parquet PATH]

Limited to the `--top-n` (default 5) 15/9-F-* wellbores with the most report-days, so
the Ollama extraction queue stays bounded for the finale week (see `run_volve.ps1`).
The full F-series set (21 wellbores, ~1,384 report-days) is available in the parquet
if this is widened later.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from nwis.config import settings
from nwis.external.volve.wells import DEFAULT_PARQUET, _parse_md, sanitize_wellbore_id

VOLVE_DIR = settings.external_dir / "volve"
DOCS_DIR = VOLVE_DIR / "docs"
TOP_N_DEFAULT = 5

_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
def load_f_series(parquet_path: Path = DEFAULT_PARQUET) -> pd.DataFrame:
    df = pd.read_parquet(parquet_path)
    f = df[df["nameWell"].str.startswith("NO 15/9-F", na=False)].copy()
    f["well_id"] = f["nameWellbore"].map(sanitize_wellbore_id)
    f["report_date"] = f["dTimStart"].str.slice(0, 10)
    return f.sort_values(["well_id", "report_date"]).reset_index(drop=True)


def top_wellbores(f: pd.DataFrame, top_n: int = TOP_N_DEFAULT) -> list[str]:
    counts = f.groupby("well_id")["report_date"].nunique().sort_values(ascending=False)
    return list(counts.head(top_n).index)


def _end_md(row) -> Optional[float]:
    status = row.get("statusInfo")
    if status is None or len(status) == 0:
        return None
    return _parse_md(status[0].get("md"))


def _activity_lines(row) -> list[str]:
    acts = row.get("activity")
    if acts is None or len(acts) == 0:
        return []
    items = sorted(acts, key=lambda a: a.get("dTimStart") or "")
    lines: list[str] = []
    for a in items:
        comment = (a.get("comments") or "").strip()
        if not comment:
            continue
        lines.append(_WS_RE.sub(" ", comment))
    return lines


def render_ddr_txt(
    well_id: str,
    wellbore_name: str,
    report_date: str,
    day_no: int,
    start_md: Optional[float],
    end_md: Optional[float],
    lines: list[str],
) -> str:
    start_txt = f"{start_md:.0f}" if start_md is not None else "?"
    end_txt = f"{end_md:.0f}" if end_md is not None else "?"
    progress_txt = (
        f"{end_md - start_md:.0f}" if start_md is not None and end_md is not None else "?"
    )
    header = (
        "DAILY DRILLING REPORT (DDR)\n"
        f"Well: {well_id}  ({wellbore_name})   Field: Volve   Basin: North Sea (Volve)\n"
        f"Report Date: {report_date}   Report Day: {day_no}\n"
        f"Depth: {start_txt} -> {end_txt} m MD  (progress {progress_txt} m)\n"
        "Current Fm: Unknown (Sodir formation tops not available for 15/9-F-* "
        "wellbores; see data/external/volve/README.md)\n"
        + "-" * 70 + "\n"
        "OPERATIONS SUMMARY:\n"
    )
    body = "\n".join(f"  {l}" for l in lines) if lines else "  (no activity narrative recorded for this report day)"
    footer = "\n" + "-" * 70 + "\n"
    return header + body + footer


def build_documents(
    f: pd.DataFrame, well_ids: list[str], docs_dir: Path
) -> tuple[list[dict], dict[str, dict]]:
    """`f` needs `well_id`, `report_date`, `nameWellbore`, `statusInfo`, `activity`
    columns (see `load_f_series`). Writes one `.txt` per (well_id, report_date) under
    `docs_dir/<well_id>/DDR_<date>.txt`. Returns (documents.json entries, per-well
    summary dict)."""
    docs_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    summary: dict[str, dict] = {}

    for well_id in well_ids:
        wdf = f[f["well_id"] == well_id].sort_values("report_date")
        out_dir = docs_dir / well_id
        out_dir.mkdir(parents=True, exist_ok=True)

        prev_end: Optional[float] = None
        n_lines_total = 0
        wellbore_name = well_id
        for day_no, (_, row) in enumerate(wdf.iterrows(), start=1):
            wellbore_name = str(row["nameWellbore"]).replace("NO ", "")
            end_md = _end_md(row)
            start_md = prev_end if prev_end is not None else (0.0 if end_md is not None else None)
            lines = _activity_lines(row)
            n_lines_total += len(lines)

            text = render_ddr_txt(
                well_id, wellbore_name, row["report_date"], day_no, start_md, end_md, lines
            )
            fname = f"DDR_{row['report_date']}.txt"
            (out_dir / fname).write_text(text, encoding="utf-8")

            manifest.append({
                "well_id": well_id,
                "doc_type": "DDR",
                "path": f"docs/{well_id}/{fname}",
                "report_date": row["report_date"],
                "n_pages": 1,
                "is_scanned": False,
            })
            if end_md is not None:
                prev_end = end_md

        summary[well_id] = {
            "wellbore_name": wellbore_name,
            "n_days": len(wdf),
            "n_narrative_lines": n_lines_total,
        }
    return manifest, summary


def write_summary(summary: dict[str, dict], out_path: Path) -> None:
    lines = [
        "# Volve DDR docs -- SUMMARY (Phase 8)",
        "",
        "Generated by `nwis/external/volve/prepare_docs.py` from the HuggingFace parquet "
        "mirror of the Equinor Volve DDR release (licence + provenance in `README.md`). "
        f"Limited to the {len(summary)} wellbore(s) with the most report-days in the "
        "15/9-F-* subset.",
        "",
        "| well_id | wellbore | report-days | narrative lines |",
        "|---|---|---|---|",
    ]
    total_days = total_lines = 0
    for well_id, s in summary.items():
        lines.append(f"| {well_id} | {s['wellbore_name']} | {s['n_days']} | {s['n_narrative_lines']} |")
        total_days += s["n_days"]
        total_lines += s["n_narrative_lines"]
    lines.append(f"| **total** |  | **{total_days}** | **{total_lines}** |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Prepare Volve DDR .txt documents for nwis.ingest.run.")
    parser.add_argument("--parquet", type=str, default=str(DEFAULT_PARQUET))
    parser.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    args = parser.parse_args(argv)

    parquet_path = Path(args.parquet)
    if not parquet_path.exists():
        raise SystemExit(f"parquet not found: {parquet_path} -- run `python -m nwis.external.volve.download` first")

    f = load_f_series(parquet_path)
    well_ids = top_wellbores(f, args.top_n)
    print(f"Top {len(well_ids)} wellbores by report-day count: {well_ids}")

    manifest, summary = build_documents(f, well_ids, DOCS_DIR)
    (VOLVE_DIR / "documents.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_summary(summary, VOLVE_DIR / "SUMMARY.md")

    print(f"Wrote {len(manifest)} documents across {len(well_ids)} wells to {DOCS_DIR}")
    print(f"Manifest: {VOLVE_DIR / 'documents.json'}")
    print(f"Summary: {VOLVE_DIR / 'SUMMARY.md'}")


if __name__ == "__main__":
    main()
