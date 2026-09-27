"""Download the Volve DDR parquet + Sodir FactPages CSVs for Phase 8 real-data
validation (research/07_prior_art_deep.md "Volve DDR access recipe", Path A).

    python -m nwis.external.volve.download [--force]

Sources:
- DDR parquet: HuggingFace mirror `bengsoon/volve_daily_drilling_report`, the
  already-flattened rendering of Equinor's WITSML DDR release DrillScribe's own
  fetch_data.py pulls from (research/07 §2.5/§2.2), CC-BY-4.0, ~2 MB.
- Sodir FactPages CSV exports: `wellbore_exploration_all`, `wellbore_development_all`
  (coordinates, KB elevation, TD, status) and `wellbore_formation_top` (stratigraphic
  picks) -- the exact CSV export endpoint (`/public?/Factpages/.../rs:Format=CSV`) was
  reverse-engineered from the "Export" buttons rendered on
  https://factpages.sodir.no/en/wellbore/TableView/Development (and /Exploration/All)
  during this build (26 Sep 2026); not documented anywhere in research/03, which only
  cites the human-facing TableView URL.

Network note (26 Sep 2026 build): both huggingface.co and factpages.sodir.no resolved
fine from this machine's Bash-tool/httpx sandbox -- unlike github.com, which is known
to fail from Bash here. No PowerShell fallback was needed in practice. If httpx raises
a connection error here, retry the same URLs via PowerShell's Invoke-WebRequest (or
Start-BitsTransfer) to the same destination paths printed below, or fall back to
research/07 Path B (raw WITSML XML from the Equinor Volve Data Village release) /
Path C (BSEE) as documented in that file.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import httpx

from nwis.config import settings

VOLVE_DIR = settings.external_dir / "volve"
SODIR_DIR = VOLVE_DIR / "sodir"

DDR_PARQUET_URL = (
    "https://huggingface.co/datasets/bengsoon/volve_daily_drilling_report/"
    "resolve/main/data/all-00000-of-00001.parquet"
)

SODIR_EXPORT_URL_TEMPLATE = (
    "https://factpages.sodir.no/public?/Factpages/external/tableview/{report}"
    "&rs:Command=Render&rc:Toolbar=false&rc:Parameters=f&IpAddress=not_used"
    "&CultureCode=en&rs:Format=CSV&Top100=false"
)

# report name (Sodir's own internal name, matches the task brief's naming) -> local filename
SODIR_REPORTS: dict[str, str] = {
    "wellbore_exploration_all": "wellbore_exploration_all.csv",
    "wellbore_development_all": "wellbore_development_all.csv",
    "wellbore_formation_top": "wellbore_formation_top.csv",
}

README_TEXT = """\
# Volve data (Phase 8 real-data validation) -- LICENCE + PROVENANCE

This folder is downloaded, derived, or generated from public data and is **git-ignored**
(see `.gitignore`: `data/external/`). It is kept entirely separate from
`data/synthetic/` (the illustrative Upper Assam demo) and from `data/nwis.sqlite`
(the synthetic-only database) -- Volve wells/events live in their own
`data/volve.sqlite`. See `docs/IMPLEMENTATION_PLAN.md` Phase 8 and
`research/07_prior_art_deep.md` ("Volve DDR access recipe") for how this was built.

## Sources and licences

1. **`ddr.parquet`** -- Equinor Volve Daily Drilling Report data, via the HuggingFace
   mirror `bengsoon/volve_daily_drilling_report`
   (https://huggingface.co/datasets/bengsoon/volve_daily_drilling_report), a
   pre-flattened rendering of Equinor's own WITSML DDR release. Dataset card licence:
   **CC-BY-4.0**. The underlying data is Equinor's 2018 Volve open-data release, itself
   under the **Equinor Open Data Licence** -- explicitly granted "for research, study
   and education" to academic institutions, students and researchers (verbatim from
   equinor.com/energy/volve-data-sharing, see research/03_public_datasets.md §1). Not
   for resale; attribute Equinor when this data appears in the deck.

2. **`sodir/wellbore_exploration_all.csv`, `sodir/wellbore_development_all.csv`,
   `sodir/wellbore_formation_top.csv`** -- Norwegian Offshore Directorate (Sodir,
   formerly NPD) FactPages bulk CSV exports (https://factpages.sodir.no), used here
   for wellbore coordinates / KB elevation / total depth / status and a
   field-representative formation-top reference. Public, free, no login, synced daily
   from Sodir's own database. Licence: **NLOD (Norwegian Licence for Open Government
   Data)** -- attribute "Norwegian Offshore Directorate" when reused.

## What this buys us

Volve is a genuinely independent, real drilling dataset -- unlike `data/synthetic/`,
none of the DDR narrative text, depths, or events here were written by our own
generator. It validates the ingestion pipeline (`nwis/ingest/run.py` unchanged) against
real operator narrative, at the cost of: (a) real North Sea formation names that do
NOT fit the frozen Upper-Assam-only `nwis.schema.FORMATIONS` taxonomy (see
`nwis/external/volve/wells.py` docstring -- formation tops are NOT loaded into the DB
for this reason, only kept as a reference JSON), and (b) weak/regex ground truth
instead of human-adjudicated labels (see `nwis/external/volve/labels.py` docstring for
the exact limitations -- do not over-claim these eval numbers).

## Files

- `ddr.parquet` -- raw HuggingFace mirror, 1,759 rows (all Volve wellbores + report
  dates), one row per (wellbore, report date).
- `sodir/*.csv` -- raw Sodir FactPages bulk exports (unfiltered, all NCS wellbores).
- `docs/<well_id>/DDR_<date>.txt`, `documents.json`, `SUMMARY.md` -- generated by
  `prepare_docs.py`, consumed unchanged by `nwis.ingest.run`.
- `weak_truth.json` -- generated by `labels.py`, consumed by `nwis.ingest.eval`.
- `formation_tops_reference.json` -- generated by `wells.py`; reference only, not in the DB.
"""


def _download(url: str, dest: Path, *, force: bool = False, min_bytes: int = 1000) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        print(f"  [skip] {dest.name} exists ({dest.stat().st_size} bytes) -- pass --force to re-download")
        return dest
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    size = dest.stat().st_size
    if size < min_bytes:
        raise RuntimeError(
            f"{dest.name} downloaded but suspiciously small ({size} bytes) -- "
            f"likely an error/redirect page, not the real file. Check {url} manually."
        )
    print(f"  [ok] {dest.name}: {size:,} bytes")
    return dest


def download_ddr_parquet(force: bool = False) -> Path:
    return _download(DDR_PARQUET_URL, VOLVE_DIR / "ddr.parquet", force=force, min_bytes=100_000)


def download_sodir_csvs(force: bool = False) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for report, filename in SODIR_REPORTS.items():
        url = SODIR_EXPORT_URL_TEMPLATE.format(report=report)
        out[report] = _download(url, SODIR_DIR / filename, force=force, min_bytes=1000)
    return out


def write_readme() -> Path:
    p = VOLVE_DIR / "README.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(README_TEXT, encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Download Volve DDR parquet + Sodir FactPages CSVs.")
    parser.add_argument("--force", action="store_true", help="Re-download even if files already exist")
    args = parser.parse_args(argv)

    print("Downloading Volve DDR parquet (HuggingFace mirror, research/07 Path A)...")
    try:
        download_ddr_parquet(force=args.force)
    except Exception as e:  # noqa: BLE001
        print(f"BLOCKED: DDR parquet download failed: {e}")
        print("  Retry via PowerShell: Invoke-WebRequest -Uri <DDR_PARQUET_URL> -OutFile data/external/volve/ddr.parquet")
        print("  or fall back to research/07 Path B (raw WITSML XML) / Path C (BSEE).")
        raise SystemExit(1)

    print("Downloading Sodir FactPages CSVs (coordinates + formation-tops reference)...")
    try:
        download_sodir_csvs(force=args.force)
    except Exception as e:  # noqa: BLE001
        print(f"BLOCKED: Sodir CSV download failed: {e}")
        print("  Retry via PowerShell: Invoke-WebRequest -Uri <url> -OutFile data/external/volve/sodir/<name>.csv")
        raise SystemExit(1)

    readme = write_readme()
    print(f"Done. Licence + provenance documented in {readme}")


if __name__ == "__main__":
    main()
