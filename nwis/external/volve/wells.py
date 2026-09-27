"""Build Well / SurveyStation objects for the Volve 15/9-F-* wellbores present in the
DDR parquet, from Sodir FactPages CSV exports, and load them into the SEPARATE
`data/volve.sqlite` database -- never into `data/nwis.sqlite` (the synthetic db).

    python -m nwis.external.volve.wells [--db data/volve.sqlite]

Coordinates / KB elevation / total depth / status come from
`data/external/volve/sodir/wellbore_development_all.csv`, joined to the DDR parquet by
the Sodir "NPD number" embedded in each row's `wellboreAlias` (robust to the DDR
parquet's own wellbore-name spelling, e.g. "15/9-F-11 T2" vs. Sodir's "15/9-F-11" --
both carry NPD number 7078).

FORMATION TOPS -- important, read before extending this: Sodir's bulk
`wellbore_formation_top` export has **no picks for the 15/9-F-* development wellbores
themselves** (only for the field's exploration/appraisal wellbores, 15/9-1 .. 15/9-25;
verified by direct query during this build, 26 Sep 2026). `nwis.schema.FormationTop`
validates `formation` against `nwis.schema.FORMATIONS`, the frozen Upper-Assam-only
stratigraphic column (Alluvium ... Basement) -- Volve's real North Sea formations
(Hugin, Draupne, Skagerrak, Utsira, ...) are simply not members of that list, and
`nwis/schema.py` is frozen (do not modify per docs/IMPLEMENTATION_PLAN.md ground
rules). Mapping a real Norwegian reservoir formation onto a fake Assam name to satisfy
the validator would be a fabrication, not an approximation -- so this module does NOT
insert `FormationTop` rows for Volve wells at all. It instead writes the
field-representative picks from **15/9-19 A** (the Volve discovery/appraisal
wellbore, ~700 m from the F-series wellhead, same field/structure, FORMATION-level
rows only) to `data/external/volve/formation_tops_reference.json` for human reference.
Every Volve `DrillingEvent` extracted later will simply carry `formation=None`
(`nwis.ingest.normalise.canonical_formation` correctly fails to resolve a North-Sea
name against the Assam list and returns `None`, rather than raising) -- this is an
honest, documented limitation of validating this schema against a different basin, not
a bug.

SURVEYS -- real directional survey stations ARE present in the DDR parquet's
`surveyStation` column, accumulated across each wellbore's report-day rows (sentinel
values -999/-999.99/-9999 filtered). A wellbore with zero recovered stations falls
back to "vertical" (no `SurveyStation` rows inserted for it --
`nwis.ingest.normalise.md_to_tvd` already treats an empty survey set as TVD==MD) --
this is printed explicitly by `main()`, never silent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from nwis import db, schema as S
from nwis.config import settings

VOLVE_DIR = settings.external_dir / "volve"
SODIR_DIR = VOLVE_DIR / "sodir"
DEFAULT_PARQUET = VOLVE_DIR / "ddr.parquet"
DEFAULT_DB = settings.root / "data" / "volve.sqlite"

REFERENCE_TOPS_WELLBORE = "15/9-19 A"  # Volve discovery/appraisal wellbore, FORMATION-level only
SENTINELS = {-999.0, -999.99, -9999.0}


# --------------------------------------------------------------------------- #
def sanitize_wellbore_id(name: str) -> str:
    """'NO 15/9-F-11 T2' -> '15_9-F-11-T2'. Deterministic, filesystem- and well_id-safe."""
    n = name.strip()
    if n.upper().startswith("NO "):
        n = n[3:]
    return n.replace("/", "_").replace(" ", "-")


def _parse_md(v) -> Optional[float]:
    """Parse a WITSML-ish numeric field, filtering the -999/-999.99/-9999 sentinel
    convention both OffsetEye and TADI independently hit on this same corpus
    (research/07 §1.7 point 3)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f in SENTINELS or f <= -900:
        return None
    return f


def npd_number_from_alias(alias) -> Optional[int]:
    """Each DDR parquet row carries `wellboreAlias`, a list of {name, namingSystem}
    dicts; one entry is always `{"namingSystem": "NPD number", "name": "<int>"}` --
    the robust join key back to Sodir FactPages."""
    if alias is None:
        return None
    for a in alias:
        if a.get("namingSystem") == "NPD number":
            try:
                return int(a["name"])
            except (TypeError, ValueError):
                continue
    return None


# --------------------------------------------------------------------------- #
def load_dev_wellbores(sodir_dir: Path = SODIR_DIR) -> pd.DataFrame:
    return pd.read_csv(sodir_dir / "wellbore_development_all.csv")


def load_formation_tops_reference(
    sodir_dir: Path = SODIR_DIR, wellbore_name: str = REFERENCE_TOPS_WELLBORE
) -> list[dict]:
    tops = pd.read_csv(sodir_dir / "wellbore_formation_top.csv")
    sub = tops[(tops["wlbName"] == wellbore_name) & (tops["lsuLevel"] == "FORMATION")]
    sub = sub.sort_values("lsuTopDepth")
    out = []
    for _, r in sub.iterrows():
        out.append({
            "formation": r["lsuName"],
            "top_md_m": float(r["lsuTopDepth"]),
            "base_md_m": float(r["lsuBottomDepth"]) if pd.notna(r["lsuBottomDepth"]) else None,
        })
    return out


def load_ddr_f_series(parquet_path: Path = DEFAULT_PARQUET) -> pd.DataFrame:
    """All DDR rows for the 15/9-F-* wellbores, with `well_id`/`npd_number` added."""
    df = pd.read_parquet(parquet_path)
    f = df[df["nameWell"].str.startswith("NO 15/9-F", na=False)].copy()
    f["well_id"] = f["nameWellbore"].map(sanitize_wellbore_id)
    f["npd_number"] = f["wellboreAlias"].map(npd_number_from_alias)
    return f


# --------------------------------------------------------------------------- #
def collect_surveys(f: pd.DataFrame) -> dict[str, list[S.SurveyStation]]:
    """Accumulate every recovered (md, inc, azi) triple per well_id across all of that
    wellbore's report rows, dedup by md (last write wins), sorted by md."""
    by_well: dict[str, dict[float, tuple[float, float]]] = {}
    for _, row in f.iterrows():
        well_id = row["well_id"]
        stations = row.get("surveyStation")
        if stations is None or len(stations) == 0:
            continue
        bucket = by_well.setdefault(well_id, {})
        for st in stations:
            md = _parse_md(st.get("md"))
            inc = _parse_md(st.get("incl"))
            azi = _parse_md(st.get("azi"))
            if md is None or inc is None or azi is None:
                continue
            bucket[md] = (inc, azi)

    out: dict[str, list[S.SurveyStation]] = {}
    for well_id, bucket in by_well.items():
        out[well_id] = [
            S.SurveyStation(well_id=well_id, md_m=md, inc_deg=inc, azi_deg=azi)
            for md, (inc, azi) in sorted(bucket.items())
        ]
    return out


def _trajectory_type(stations: list[S.SurveyStation]) -> S.TrajectoryType:
    if not stations:
        return S.TrajectoryType.vertical
    max_inc = max(s.inc_deg for s in stations)
    if max_inc >= 60:
        return S.TrajectoryType.horizontal
    if max_inc >= 5:
        return S.TrajectoryType.deviated
    return S.TrajectoryType.vertical


def build_wells(
    f: pd.DataFrame, dev_df: pd.DataFrame, surveys_by_well: dict[str, list[S.SurveyStation]]
) -> tuple[list[S.Well], list[str]]:
    """Returns (wells, well_ids_using_vertical_fallback). `f` needs `well_id`,
    `nameWellbore`, `npd_number` columns (see `load_ddr_f_series`); `dev_df` is the raw
    Sodir `wellbore_development_all.csv` frame."""
    dev_by_npd = dev_df.drop_duplicates("wlbNpdidWellbore").set_index("wlbNpdidWellbore")

    wells: list[S.Well] = []
    vertical_fallback: list[str] = []

    index = f.drop_duplicates("well_id")[["well_id", "nameWellbore", "npd_number"]]
    for _, r in index.iterrows():
        well_id = r["well_id"]
        npd = r["npd_number"]
        wellbore_name = str(r["nameWellbore"]).replace("NO ", "")

        if npd is None or npd not in dev_by_npd.index:
            print(f"  [skip well] {well_id}: no Sodir wellbore_development_all match for NPD id {npd}")
            continue
        row = dev_by_npd.loc[npd]

        stations = surveys_by_well.get(well_id, [])
        if not stations:
            vertical_fallback.append(well_id)

        td = row.get("wlbTotalDepth")
        if pd.isna(td):
            td = row.get("wlbFinalVerticalDepth")
        if pd.isna(td) and stations:
            td = max(s.md_m for s in stations)
        if pd.isna(td):
            td = 0.0

        kb = row.get("wlbKellyBushElevation")
        kb = float(kb) if pd.notna(kb) else 0.0

        status_raw = str(row.get("wlbStatus") or "unknown").strip().lower().replace(" ", "_") or "unknown"

        wells.append(S.Well(
            well_id=well_id,
            name=wellbore_name,
            field="Volve",
            lat=float(row["wlbNsDecDeg"]),
            lon=float(row["wlbEwDecDeg"]),
            kb_elev_m=kb,
            td_md_m=float(td),
            trajectory_type=_trajectory_type(stations),
            status=status_raw,
            basin="North Sea (Volve)",
        ))
    return wells, vertical_fallback


def load_wells_and_surveys(
    parquet_path: Path = DEFAULT_PARQUET, sodir_dir: Path = SODIR_DIR
) -> tuple[list[S.Well], dict[str, list[S.SurveyStation]], list[str]]:
    f = load_ddr_f_series(parquet_path)
    dev_df = load_dev_wellbores(sodir_dir)
    surveys_by_well = collect_surveys(f)
    wells, vertical_fallback = build_wells(f, dev_df, surveys_by_well)
    return wells, surveys_by_well, vertical_fallback


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Load Volve wells/surveys into data/volve.sqlite.")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB))
    parser.add_argument("--parquet", type=str, default=str(DEFAULT_PARQUET))
    parser.add_argument("--sodir-dir", type=str, default=str(SODIR_DIR))
    args = parser.parse_args(argv)

    parquet_path = Path(args.parquet)
    sodir_dir = Path(args.sodir_dir)
    if not parquet_path.exists() or not sodir_dir.exists():
        raise SystemExit("Missing inputs -- run `python -m nwis.external.volve.download` first.")

    wells, surveys_by_well, vertical_fallback = load_wells_and_surveys(parquet_path, sodir_dir)
    tops_reference = load_formation_tops_reference(sodir_dir)

    db_path = Path(args.db)
    db.engine(db_path)  # bind the module-level engine to the SEPARATE Volve db; never db.reset()

    n_wells = db.upsert_wells(wells)
    n_surveys = 0
    for w in wells:
        stations = surveys_by_well.get(w.well_id, [])
        if stations:
            n_surveys += db.add_surveys(stations)

    ref_path = VOLVE_DIR / "formation_tops_reference.json"
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(json.dumps({
        "source_wellbore": REFERENCE_TOPS_WELLBORE,
        "note": (
            "Reference only -- NOT loaded into formation_tops. Volve's real North Sea "
            "formation names are not members of nwis.schema.FORMATIONS (Upper Assam, "
            "frozen). Applied field-wide since 15/9-19 A and every 15/9-F-* wellbore "
            "share the same Volve structure at essentially the same wellhead location."
        ),
        "tops": tops_reference,
    }, indent=2), encoding="utf-8")

    print(f"Loaded {n_wells} wells, {n_surveys} survey stations into {db_path}")
    if vertical_fallback:
        print(
            f"Vertical fallback (no recovered survey stations) for {len(vertical_fallback)} "
            f"well(s): {vertical_fallback}"
        )
    print(f"Formation tops: NOT loaded into DB (schema mismatch, see module docstring). "
          f"Reference picks ({len(tops_reference)} rows) written to {ref_path}")


if __name__ == "__main__":
    main()
