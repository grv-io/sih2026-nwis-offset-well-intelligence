"""CLI: ingest a folder (or single file) of DDR/WCR documents into the NWIS db.

    python -m nwis.ingest.run <folder_or_pdf> [--well-id X] [--limit N] [--force]

well_id resolution order: --well-id flag > documents.json entry > inferred from the
path (`data/synthetic/docs/<well_id>/...` -> `<well_id>`).

Resumable: each document gets a deterministic id (uuid5 of well_id+filename); if that
id already exists in the `documents` table, the document is skipped unless --force.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import text as sql_text

from nwis import db, schema as S
from nwis.ingest.chunk import chunk_pages
from nwis.ingest.extract_events import extract_from_chunk
from nwis.ingest.extract_text import pdf_to_pages
from nwis.ingest.normalise import build_drilling_event

_DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_DOC_TYPE_RE = re.compile(r"(DDR|WCR|MUD[_-]?LOG)", re.IGNORECASE)


def _infer_well_id(p: Path) -> Optional[str]:
    parts = p.parts
    if "docs" in parts:
        idx = parts.index("docs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _infer_doc_type(name: str) -> str:
    m = _DOC_TYPE_RE.search(name)
    if not m:
        return "other"
    tag = m.group(1).upper().replace("-", "_")
    if tag.startswith("MUD"):
        return "mud_log"
    return tag


def _infer_report_date(name: str) -> Optional[date]:
    m = _DATE_IN_NAME_RE.search(name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _document_id(well_id: str, filename: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{well_id}:{filename}"))


def _document_exists(document_id: str) -> bool:
    with db.session() as s:
        return s.get(db.DocRow, document_id) is not None


def _discover_files(root: Path, well_id_override: Optional[str]) -> list[tuple[Path, S.SourceDocument]]:
    """Return (path, SourceDocument) pairs, using documents.json if present, else a
    recursive glob for .pdf/.txt files."""
    out: list[tuple[Path, S.SourceDocument]] = []

    if root.is_file():
        files = [root]
        base_dir = root.parent
    else:
        # Manifest may sit in the docs folder or one level above it (data/synthetic/documents.json
        # describes data/synthetic/docs/**). Without this, a bare glob ingests every .pdf AND its
        # .txt twin as two separate documents (bug found 26 Sep: 472 files instead of 236).
        manifest = root / "documents.json"
        if not manifest.exists() and (root.parent / "documents.json").exists():
            manifest = root.parent / "documents.json"
        if manifest.exists():
            entries = json.loads(manifest.read_text(encoding="utf-8"))
            for e in entries:
                well_id = well_id_override or e.get("well_id") or _infer_well_id(root)
                path = Path(e["path"])
                if not path.is_absolute():
                    for cand in (manifest.parent / path, Path.cwd() / path, root / path,
                                 manifest.parent.parent / path):
                        if cand.exists():
                            path = cand
                            break
                    else:
                        path = Path.cwd() / path
                doc = S.SourceDocument(
                    document_id=e.get("document_id") or _document_id(well_id, path.name),
                    well_id=well_id,
                    doc_type=e.get("doc_type", _infer_doc_type(path.name)),
                    path=_stored_path(path),
                    report_date=e.get("report_date") and date.fromisoformat(e["report_date"]),
                    n_pages=e.get("n_pages", 1),
                    is_scanned=e.get("is_scanned", False),
                )
                out.append((path, doc))
            return out
        files = sorted(list(root.rglob("*.pdf")) + list(root.rglob("*.txt")))
        base_dir = root

    for path in files:
        well_id = well_id_override or _infer_well_id(path) or _infer_well_id(base_dir)
        if not well_id:
            print(f"  [skip] {path}: cannot infer well_id (pass --well-id)")
            continue
        doc = S.SourceDocument(
            document_id=_document_id(well_id, path.name),
            well_id=well_id,
            doc_type=_infer_doc_type(path.name),
            path=_stored_path(path),
            report_date=_infer_report_date(path.name),
            n_pages=1,
            is_scanned=False,
        )
        out.append((path, doc))
    return out


def _stored_path(path: Path) -> str:
    """Path as stored in documents.path: repo-relative (POSIX) when the file lives
    inside the repo, so the committed database carries no machine-specific paths;
    api/ui_router.py resolves relative paths against the repo root."""
    from nwis.config import settings

    try:
        return Path(path).resolve().relative_to(settings.root).as_posix()
    except ValueError:
        return str(path)


def _dedup_events(events: list[S.DrillingEvent], depth_tol_m: float = 15.0) -> list[S.DrillingEvent]:
    """Chunking is overlap-y (100-char overlap, plus a day's NPT-summary line often
    re-states an event already described earlier the same day) so the same real-world
    event can get extracted more than once per document. Collapse same-document,
    same-event_type events whose depth agrees within `depth_tol_m` (or that both lack a
    depth) into the single highest-confidence extraction.
    """
    kept: list[S.DrillingEvent] = []
    for ev in sorted(events, key=lambda e: -e.extraction_confidence):
        is_dup = False
        for k in kept:
            if k.event_type != ev.event_type:
                continue
            if ev.depth_md_m is None and k.depth_md_m is None:
                is_dup = True
            elif ev.depth_md_m is not None and k.depth_md_m is not None and abs(ev.depth_md_m - k.depth_md_m) <= depth_tol_m:
                is_dup = True
            if is_dup:
                break
        if not is_dup:
            kept.append(ev)
    return kept


def _clear_document(document_id: str) -> None:
    """Delete any events/chunks (+ their review_queue/FTS rows) already stored for this
    document_id. Event ids are content-hashed (uuid5 of doc+page+quote), so a re-extraction
    with slightly different LLM wording would otherwise leave old rows behind instead of
    overwriting them — this makes --force actually idempotent per document.
    """
    with db.engine().connect() as conn:
        conn.execute(sql_text(
            "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE document_id = :d)"
        ).bindparams(d=document_id))
        # Only machine-extracted rows are replaced. synthetic_truth (eval ground truth) and
        # human_reviewed rows must survive a --force re-run (bug found 26 Sep: truth 182 -> 110).
        keep = "('synthetic_truth', 'human_reviewed')"
        conn.execute(sql_text(
            "DELETE FROM review_queue WHERE event_id IN (SELECT event_id FROM events "
            f"WHERE source_document_id = :d AND extraction_method NOT IN {keep})"
        ).bindparams(d=document_id))
        conn.execute(sql_text("DELETE FROM chunks WHERE document_id = :d").bindparams(d=document_id))
        conn.execute(sql_text(
            f"DELETE FROM events WHERE source_document_id = :d AND extraction_method NOT IN {keep}"
        ).bindparams(d=document_id))
        conn.commit()


def ingest_document(path: Path, doc: S.SourceDocument) -> tuple[int, int, int]:
    """Returns (n_chunks, n_events, n_low_confidence)."""
    _clear_document(doc.document_id)
    pages = pdf_to_pages(path)
    chunks = chunk_pages(pages, pdf_name=path.name)
    doc = doc.model_copy(update={"n_pages": len(pages) or doc.n_pages,
                                  "is_scanned": any(p.method == "ocr" for p in pages)})
    db.upsert_documents([doc])

    well_context = {"well_id": doc.well_id, "doc_type": doc.doc_type, "report_date": doc.report_date}

    events: list[S.DrillingEvent] = []
    for chunk in chunks:
        llm_events = extract_from_chunk(chunk, well_context)
        for llm_ev in llm_events:
            events.append(build_drilling_event(
                llm_ev,
                well_id=doc.well_id,
                source_document_id=doc.document_id,
                source_page_ref=chunk.page_ref,
                report_date=doc.report_date,
            ))

    events = _dedup_events(events)

    n_low_conf = sum(1 for e in events if e.extraction_confidence < 0.6)
    if events:
        db.upsert_events(events)

    if chunks:
        texts = [c.text for c in chunks]
        vecs = llm_module_embed(texts)
        rows = [
            db.ChunkRow(
                chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.document_id}|{c.page_ref}|{i}")),
                document_id=doc.document_id,
                well_id=doc.well_id,
                page_ref=c.page_ref,
                text=c.text,
                embedding_json=json.dumps(vec),
            )
            for i, (c, vec) in enumerate(zip(chunks, vecs))
        ]
        db.add_chunks(rows)

    return len(chunks), len(events), n_low_conf


def llm_module_embed(texts: list[str]) -> list[list[float]]:
    from nwis.llm import embed
    return embed(texts)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest DDR/WCR documents into NWIS.")
    parser.add_argument("path", type=str, help="Folder or single .pdf/.txt file")
    parser.add_argument("--well-id", type=str, default=None, dest="well_id")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Re-ingest documents already in the db")
    args = parser.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        raise SystemExit(f"path not found: {root}")

    pairs = _discover_files(root, args.well_id)
    if args.limit:
        pairs = pairs[: args.limit]

    if not pairs:
        print("No documents found.")
        return

    t0 = time.time()
    n_processed = n_skipped = n_events = n_low_conf = n_chunks = n_errors = 0

    for i, (path, doc) in enumerate(pairs, start=1):
        if not args.force and _document_exists(doc.document_id):
            print(f"[{i}/{len(pairs)}] {path.name}: skipped (already ingested)")
            n_skipped += 1
            continue
        try:
            nc, ne, nl = ingest_document(path, doc)
            n_chunks += nc
            n_events += ne
            n_low_conf += nl
            n_processed += 1
            print(f"[{i}/{len(pairs)}] {path.name}: {nc} chunks, {ne} events ({nl} low-confidence)")
        except Exception as e:  # noqa: BLE001 — keep going on a bad document
            n_errors += 1
            print(f"[{i}/{len(pairs)}] {path.name}: ERROR {e}")

    dt = time.time() - t0
    # Cross-document incident linking (a DDR and the WCR mention the same problem): one
    # incident, merged mention's page ref kept in free_text. See nwis/ingest/link.py.
    if n_processed:
        try:
            from nwis.ingest.link import link_incidents
            lr = link_incidents()
            print(f"incident linking: {lr['mentions']} mentions -> {lr['incidents']} incidents ({lr['merged']} merged)")
        except Exception as exc:  # noqa: BLE001
            print(f"incident linking skipped: {exc}")
    print("\n--- summary ---")
    print(f"processed:  {n_processed}")
    print(f"skipped:    {n_skipped} (already ingested; use --force to redo)")
    print(f"errors:     {n_errors}")
    print(f"chunks:     {n_chunks}")
    print(f"events:     {n_events} ({n_low_conf} sent to review_queue)")
    print(f"elapsed:    {dt:.1f}s")


if __name__ == "__main__":
    main()
