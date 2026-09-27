"""Hybrid retrieval: reciprocal-rank fusion of SQLite FTS5 (bm25 keyword) and
cosine similarity over the numpy embedding cache, plus a separate structured
(event-table) search.

Filters dict (all optional, all keys may be omitted or None):
    well_ids: list[str]            -- restrict to these wells
    formation: str                 -- canonical formation name (nwis.schema.FORMATIONS)
    event_type: str                -- nwis.schema.EventType value
    radius_from_well: str          -- well_id to measure distance from
    radius_km: float                -- radius in km (used with radius_from_well)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from nwis import db
from nwis import llm as llm_mod
from nwis.search import index as index_mod

RRF_K = 60  # standard reciprocal-rank-fusion smoothing constant
FTS_POOL = 50
COSINE_POOL = 50

# event_type inference from free-text query. Order matters: more specific
# patterns first so "gas" doesn't swallow "gas show" ambiguity, etc.
EVENT_SYNONYMS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bstuck\b", re.I), "stuck_pipe"),
    (re.compile(r"\b(loss|losses|lost circulation|mud loss)\b", re.I), "mud_loss"),
    (re.compile(r"\b(kick|influx|blowout)\b", re.I), "kick"),
    (re.compile(r"\b(overpressure|over-pressure|high pressure)\b", re.I), "overpressure"),
    (re.compile(r"\btorque spike|torque\b", re.I), "torque_spike"),
    (re.compile(r"\bcement(ing)?\b", re.I), "cementing_issue"),
    (re.compile(r"\bfish(ing)?\b", re.I), "fishing_operation"),
    (re.compile(r"\b(instability|caving|sloughing|tight hole)\b", re.I), "wellbore_instability"),
    (re.compile(r"\bgas show|gas\b", re.I), "gas_show"),
    (re.compile(r"\btwist[- ]?off\b", re.I), "twist_off"),
    (re.compile(r"\blost bha|\bbha\b", re.I), "lost_bha"),
    (re.compile(r"\bnpt\b", re.I), "npt_other"),
]

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "at", "on", "to", "for", "what",
    "happened", "is", "was", "were", "did", "how", "why", "with", "near", "about",
}


@dataclass
class Hit:
    chunk_id: str
    well_id: str
    page_ref: str
    text: str
    score: float
    why: str = ""


@dataclass
class Filters:
    well_ids: Optional[list[str]] = None
    formation: Optional[str] = None
    event_type: Optional[str] = None
    radius_from_well: Optional[str] = None
    radius_km: Optional[float] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Filters":
        d = d or {}
        return cls(
            well_ids=d.get("well_ids"),
            formation=d.get("formation"),
            event_type=d.get("event_type"),
            radius_from_well=d.get("radius_from_well"),
            radius_km=d.get("radius_km"),
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sanitize_fts_query(query: str) -> str:
    """FTS5 chokes on bare `"()*:-^` etc. Extract alnum tokens (len>=2) and OR
    them together -- permissive recall, bm25 ranking does the rest. Returns ""
    if nothing usable remains."""
    tokens = re.findall(r"[A-Za-z0-9]+", query)
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return ""
    return " OR ".join(tokens)


def _wells_within_radius(center_well_id: str, radius_km: float) -> Optional[set[str]]:
    center = db.get_well(center_well_id)
    if center is None:
        return set()  # unknown well -> no matches, honest empty result
    out = set()
    for w in db.all_wells():
        if _haversine_km(center.lat, center.lon, w.lat, w.lon) <= radius_km:
            out.add(w.well_id)
    return out


def _allowed_well_ids(filters: Filters) -> Optional[set[str]]:
    allowed: Optional[set[str]] = set(filters.well_ids) if filters.well_ids else None
    if filters.radius_from_well and filters.radius_km is not None:
        radius_set = _wells_within_radius(filters.radius_from_well, filters.radius_km)
        allowed = radius_set if allowed is None else (allowed & radius_set)
    return allowed


def _allowed_well_page_pairs(filters: Filters) -> Optional[set[tuple[str, str]]]:
    """When formation/event_type filters are set, restrict to (well_id, page_ref)
    pairs that have a matching event on that page (chunks and events share
    page_ref because they're built from the same chunk in nwis.ingest.run)."""
    if not filters.formation and not filters.event_type:
        return None
    rows = db.events_for(event_type=filters.event_type, formation=filters.formation)
    return {(r.well_id, r.source_page_ref) for r in rows}


def _passes_filters(row: db.ChunkRow, allowed_wells: Optional[set[str]],
                     allowed_pairs: Optional[set[tuple[str, str]]]) -> bool:
    if allowed_wells is not None and row.well_id not in allowed_wells:
        return False
    if allowed_pairs is not None and (row.well_id, row.page_ref) not in allowed_pairs:
        return False
    return True


# --------------------------------------------------------------------------- #
# hybrid search
# --------------------------------------------------------------------------- #
def hybrid_search(query: str, k: int = 8, filters: Optional[dict] = None) -> list[Hit]:
    f = Filters.from_dict(filters)
    allowed_wells = _allowed_well_ids(f)
    allowed_pairs = _allowed_well_page_pairs(f)

    chunk_rows = {c.chunk_id: c for c in db.all_chunks()}

    # ---- FTS ranking -------------------------------------------------------
    fts_rank: dict[str, int] = {}
    fts_query = sanitize_fts_query(query)
    if fts_query:
        for rank, cid in enumerate(db.fts_search(fts_query, limit=FTS_POOL)):
            if cid in chunk_rows:
                fts_rank[cid] = rank

    # ---- cosine ranking -----------------------------------------------------
    cosine_rank: dict[str, int] = {}
    cosine_score: dict[str, float] = {}
    ids, matrix = index_mod.load(rebuild_if_missing=True)
    if ids and matrix.size:
        qvec = np.array(llm_mod.embed([query])[0], dtype=np.float32)
        dim = matrix.shape[1]
        if qvec.shape[0] != dim:
            qvec = (list(qvec) + [0.0] * dim)[:dim]
            qvec = np.array(qvec, dtype=np.float32)
        q_norm = np.linalg.norm(qvec)
        m_norms = np.linalg.norm(matrix, axis=1)
        denom = (m_norms * q_norm)
        with np.errstate(divide="ignore", invalid="ignore"):
            sims = np.where(denom > 0, (matrix @ qvec) / denom, 0.0)
        order = np.argsort(-sims)[:COSINE_POOL]
        for rank, idx in enumerate(order):
            cid = ids[idx]
            if cid in chunk_rows:
                cosine_rank[cid] = rank
                cosine_score[cid] = float(sims[idx])

    # ---- filter + fuse ------------------------------------------------------
    candidates = set(fts_rank) | set(cosine_rank)
    candidates = {
        cid for cid in candidates
        if _passes_filters(chunk_rows[cid], allowed_wells, allowed_pairs)
    }

    scored: list[tuple[float, str]] = []
    for cid in candidates:
        rrf = 0.0
        why_parts = []
        if cid in fts_rank:
            rrf += 1.0 / (RRF_K + fts_rank[cid] + 1)
            why_parts.append(f"fts#{fts_rank[cid] + 1}")
        if cid in cosine_rank:
            rrf += 1.0 / (RRF_K + cosine_rank[cid] + 1)
            why_parts.append(f"cosine#{cosine_rank[cid] + 1}({cosine_score[cid]:.2f})")
        scored.append((rrf, cid))

    scored.sort(key=lambda t: -t[0])

    hits: list[Hit] = []
    for rrf, cid in scored[:k]:
        row = chunk_rows[cid]
        why_bits = []
        if cid in fts_rank:
            why_bits.append(f"fts#{fts_rank[cid] + 1}")
        if cid in cosine_rank:
            why_bits.append(f"cosine#{cosine_rank[cid] + 1}({cosine_score[cid]:.2f})")
        hits.append(Hit(
            chunk_id=row.chunk_id,
            well_id=row.well_id,
            page_ref=row.page_ref,
            text=row.text,
            score=rrf,
            why=" + ".join(why_bits),
        ))
    return hits


# --------------------------------------------------------------------------- #
# structured search (events table)
# --------------------------------------------------------------------------- #
def infer_event_type(query: str) -> Optional[str]:
    for pattern, event_type in EVENT_SYNONYMS:
        if pattern.search(query):
            return event_type
    return None


def structured_hits(query: str, filters: Optional[dict] = None) -> list[db.EventRow]:
    f = Filters.from_dict(filters)
    event_type = f.event_type or infer_event_type(query)
    allowed_wells = _allowed_well_ids(f)
    well_ids = sorted(allowed_wells) if allowed_wells is not None else None

    # Formation named in the question ("Barail kick mud weight") filters the structured rows,
    # otherwise the fallback listing shows kicks from every formation (26 Sep).
    formation = f.formation
    if formation is None:
        from nwis.schema import FORMATIONS
        ql = query.lower()
        formation = next((fm for fm in FORMATIONS if fm.lower() in ql), None)

    rows = db.events_for(well_ids=well_ids, event_type=event_type, formation=formation)
    if not rows and formation and f.formation is None:
        rows = db.events_for(well_ids=well_ids, event_type=event_type)  # inferred filter too strict

    tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", query) if len(t) >= 3 and t.lower() not in _STOPWORDS]
    # drop tokens already "consumed" by the event-type synonym match so they don't
    # spuriously require e.g. "stuck" to literally appear in free_text again.
    if event_type:
        tokens = [t for t in tokens if t not in {"stuck", "pipe", "loss", "losses", "kick", "influx",
                                                    "cementing", "cement", "fishing", "gas", "npt"}]

    if not tokens:
        return rows

    def _match(r: db.EventRow) -> bool:
        haystack = " ".join(str(x or "") for x in (r.free_text, r.cause, r.remedy, r.formation)).lower()
        return any(t in haystack for t in tokens)

    keyword_matches = [r for r in rows if _match(r)]
    return keyword_matches if keyword_matches else rows
