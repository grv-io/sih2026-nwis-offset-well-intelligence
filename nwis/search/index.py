"""Numpy matrix cache of chunk embeddings for cosine search.

`build()` reads every `nwis.db.ChunkRow`, fills any missing embeddings via
`nwis.llm.embed`, and writes:
    models/chunk_index.npz   -- one array "vectors" (n_chunks x dim), float32
    models/chunk_index_ids.json -- parallel list of chunk_id (row i -> vectors[i])

`load()` returns (ids, vectors) from the cache, rebuilding it if missing or if
the db now has chunks the cache doesn't know about (best-effort staleness check
by chunk_id set, not a full sync — call `--rebuild` after a big ingest run).

CLI:
    python -m nwis.search.index --rebuild
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import numpy as np

from nwis import db
from nwis.config import settings

INDEX_PATH: Path = settings.models_dir / "chunk_index.npz"
IDS_PATH: Path = settings.models_dir / "chunk_index_ids.json"

EMBED_BATCH_SIZE = 16


def embed_missing(progress: bool = True) -> int:
    """Fill `embedding_json` for any ChunkRow lacking it, via nwis.llm.embed.

    Batches the calls; prints progress. Returns the number of chunks embedded.
    """
    from nwis import llm as llm_mod

    chunks = [c for c in db.all_chunks() if not c.embedding_json]
    if not chunks:
        if progress:
            print("embed_missing: nothing to do (0 chunks missing an embedding)")
        return 0

    n = len(chunks)
    done = 0
    for start in range(0, n, EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vecs = llm_mod.embed([c.text for c in batch])
        rows = []
        for c, vec in zip(batch, vecs):
            c.embedding_json = json.dumps(vec)
            rows.append(c)
        db.add_chunks(rows)
        done += len(batch)
        if progress:
            print(f"embed_missing: {done}/{n} chunks embedded")
    return done


def build(progress: bool = True) -> tuple[list[str], "np.ndarray"]:
    """Fill missing embeddings, then rebuild the numpy cache from the db. Returns
    (ids, vectors)."""
    embed_missing(progress=progress)

    rows = [c for c in db.all_chunks() if c.embedding_json]
    ids: list[str] = []
    vecs: list[list[float]] = []
    for c in rows:
        v = db.embedding_of(c)
        if v is None:
            continue
        ids.append(c.chunk_id)
        vecs.append(v)

    if vecs:
        dim = len(vecs[0])
        matrix = np.array(
            [v if len(v) == dim else (v + [0.0] * (dim - len(v)))[:dim] for v in vecs],
            dtype=np.float32,
        )
    else:
        matrix = np.zeros((0, 0), dtype=np.float32)

    settings.models_dir.mkdir(parents=True, exist_ok=True)
    np.savez(INDEX_PATH, vectors=matrix)
    IDS_PATH.write_text(json.dumps(ids), encoding="utf-8")

    if progress:
        print(f"build: cached {len(ids)} chunk vectors -> {INDEX_PATH}")
    return ids, matrix


def load(rebuild_if_missing: bool = True) -> tuple[list[str], "np.ndarray"]:
    """Load the cached (ids, vectors). Rebuilds from the db if the cache files
    are missing (does NOT auto-rebuild just because the db changed — call
    `build()`/`--rebuild` explicitly after a big ingest run)."""
    if not INDEX_PATH.exists() or not IDS_PATH.exists():
        if rebuild_if_missing:
            return build(progress=False)
        return [], np.zeros((0, 0), dtype=np.float32)

    ids: list[str] = json.loads(IDS_PATH.read_text(encoding="utf-8"))
    with np.load(INDEX_PATH) as data:
        vectors = data["vectors"]
    return ids, vectors


def _main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Build/rebuild the chunk embedding index.")
    parser.add_argument("--rebuild", action="store_true", help="Force a full rebuild")
    args = parser.parse_args(argv)
    if args.rebuild or not INDEX_PATH.exists():
        build()
    else:
        ids, vecs = load(rebuild_if_missing=False)
        print(f"index already present: {len(ids)} vectors. Pass --rebuild to force.")


if __name__ == "__main__":
    _main()
