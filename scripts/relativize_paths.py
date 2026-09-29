"""Rewrite machine-specific absolute paths to repo-relative ones.

The pre-ingested database and the training metrics were produced on a developer
machine, so they carried absolute Windows paths (``<drive>:\\...\\data\\synthetic\\docs\\...``).
Those paths are meaningless on any other machine and leak the local folder layout.
This script rewrites them to repo-relative POSIX paths:

* ``data/nwis.sqlite``      table ``documents``, column ``path``
                            -> ``data/synthetic/docs/<WELL>/<file>``
* ``models/predict_metrics.json``  keys ``model_path`` / ``meta_path``
                            -> ``models/<file>``

The API already resolves a relative document path against the repo root
(``api/ui_router.py::_abs``), so citations keep opening the right report page.
The database is VACUUMed afterwards so the old values do not linger in free pages.

    python scripts/relativize_paths.py            # rewrite in place
    python scripts/relativize_paths.py --check    # exit 1 if anything is still absolute
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "nwis.sqlite"
METRICS = ROOT / "models" / "predict_metrics.json"

_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")
_ANCHORS = ("data/", "models/")


def to_relative(path: str) -> str:
    """Absolute path -> repo-relative POSIX path, anchored at the last 'data/' or
    'models/' segment. Relative paths are only normalised to forward slashes."""
    p = path.replace("\\", "/")
    if not _ABS.match(p):
        return p
    low = p.lower()
    best = -1
    for anchor in _ANCHORS:
        i = low.rfind("/" + anchor)
        if i > best:
            best = i
    if best < 0:
        return Path(p).name  # nothing recognisable: keep the file name only
    return p[best + 1:]


def fix_db(check: bool) -> int:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute("SELECT document_id, path FROM documents").fetchall()
        changes = [(to_relative(path), doc_id) for doc_id, path in rows if to_relative(path) != path]
        if check:
            return len(changes)
        if changes:
            con.executemany("UPDATE documents SET path = ? WHERE document_id = ?", changes)
            con.commit()
            con.execute("VACUUM")
        print(f"documents.path: {len(changes)} of {len(rows)} rows rewritten")
        return 0
    finally:
        con.close()


def fix_metrics(check: bool) -> int:
    if not METRICS.exists():
        return 0
    data = json.loads(METRICS.read_text(encoding="utf-8"))
    n = 0

    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("model_path", "meta_path") and isinstance(v, str) and to_relative(v) != v:
                    o[k] = to_relative(v)
                    n += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    if check:
        return n
    if n:
        METRICS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"predict_metrics.json: {n} paths rewritten")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if absolute paths remain")
    args = ap.parse_args(argv)
    if args.check:
        left = fix_db(True) + fix_metrics(True)
        print(f"absolute paths remaining: {left}")
        return 1 if left else 0
    fix_db(False)
    fix_metrics(False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
