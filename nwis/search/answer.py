"""Cited RAG answer over the NWIS knowledge base.

Adopts CB-acc-tech's anti-hallucination prompt discipline + the TADI citation
rule (docs/IMPLEMENTATION_PLAN.md §6 A4): every claim must cite >=1 structured
event row AND >=1 verbatim quote in the form `[well_id | page_ref]`. If the
retrieved evidence is insufficient, the model is told to refuse rather than
guess, and a hallucination-guard post-check strips any citation tag that
doesn't correspond to a chunk we actually retrieved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from nwis import db
from nwis import llm as llm_mod
from nwis.search.retrieve import Filters, Hit, hybrid_search, structured_hits

REFUSAL_MARKER = "INSUFFICIENT_EVIDENCE"
CITATION_RE = re.compile(r"\[\s*([^\|\]]+?)\s*\|\s*([^\]]+?)\s*\]")

SYSTEM_PROMPT = """You are a senior drilling engineer answering a colleague on the eRTMAC \
desk at Oil India Limited, Duliajan. You answer ONLY from the STRUCTURED EVENTS and QUOTES \
given to you below -- never from general knowledge, never from anything you recall about \
drilling that isn't in the evidence. Never invent a depth, formation, well, or outcome that \
isn't in the evidence.

Rules:
- Every factual claim you make must be backed by at least one STRUCTURED EVENT row AND at \
least one verbatim QUOTE, cited inline as [well_id | page_ref] copied EXACTLY (character for \
character) from the evidence blocks below. Never invent a citation tag.
- When present in the evidence, mention: depth, formation, what was done (action/remedy), and \
hours of NPT lost.
- FORMAT, strictly: line 1 = the direct answer to the question in one sentence. Then at most \
5 lines, one per incident, each shaped as "WELL, depth m MD, formation, X h NPT - what was done \
[well_id | page_ref]". Plain text only: no markdown headings, no bold, no bullet symbols, no \
numbered lists, and never open with "Based on the provided information" or any preamble.
- Terse drilling-engineer voice (no fluff, no apologies, no "I'm an AI").
- If the evidence below is genuinely insufficient to answer the question, reply with EXACTLY \
the single token INSUFFICIENT_EVIDENCE and nothing else.

Example of the ONLY acceptable shape (values are illustrative):
Stuck pipe in Girujan was freed each time by working the string with max overpull and spotting a pipe-lax pill; 3-28 h NPT.
DUL-008, 978 m MD, Girujan, 28.2 h NPT - worked pipe, spotted 106 bbl pipe-lax pill, freed [DUL-008 | DUL-008_WCR.pdf#p3]
DUL-012, 1462 m MD, Girujan, 6.6 h NPT - jarred, 40 bbl pill, freed on second attempt [DUL-012 | DUL-012_WCR.pdf#p3]"""


@dataclass
class Citation:
    well_id: str
    document_id: str
    page_ref: str
    quote: str


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    structured: list[dict] = field(default_factory=list)
    refused: bool = False
    degraded: bool = False
    guard: Optional[str] = None            # "greeting" | "off_topic" when the fixed line was used
    suggestions: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Scope guard: this is archive Q&A, not chit-chat. Greetings and off-topic questions get one
# polite fixed line with example questions instead of a retrieval attempt (judge framing,
# 26 Sep: "evidence nahi hai to mana karta hai").
# --------------------------------------------------------------------------- #
EXAMPLE_QUESTIONS = [
    "Girujan stuck pipe remedy",
    "Tipam losses LCM",
    "Barail kick mud weight",
    "what happened at 2,400 m near DUL-005",
]

_GREETING_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|namaste|namaskar|good (morning|afternoon|evening)|thanks?( you)?|thank u|"
    r"ok(ay)?|how are you|who are you|what can you do|help|kya haal|kaise ho)\W*$",
    re.IGNORECASE,
)

_DRILLING_LEXICON = {
    "well", "wells", "offset", "drill", "drilling", "drilled", "bit", "trip", "tripping", "pooh", "rih",
    "mud", "weight", "ppg", "losses", "loss", "kick", "influx", "gain", "stuck", "pipe", "overpull",
    "torque", "drag", "spp", "pressure", "overpressure", "casing", "cement", "cementing", "fishing",
    "fish", "bha", "twist", "instability", "tight", "hole", "caving", "gas", "show", "npt", "rig",
    "formation", "depth", "md", "tvd", "top", "lcm", "pill", "jar", "jarring", "shut", "bop",
    "circulate", "circulated", "kill", "returns", "pit", "flow", "rop", "wob", "rpm", "survey",
    "girujan", "tipam", "barail", "kopili", "sylhet", "namsang", "dhekiajuli", "alluvium", "basement",
    "duliajan", "moran", "naharkatiya", "assam", "volve", "ddr", "wcr", "report", "archive", "incident",
    "event", "events", "problem", "problems", "remedy", "cause", "hours", "lost", "happened", "history",
    "freed", "jarred", "worked", "spotted", "pumped", "influx", "fracture", "shale", "sand", "clay", "coal",
}
_WELL_ID_RE = re.compile(r"\b[A-Z]{3}-\d{3}\b|\b15[/_]9-F-\d+", re.IGNORECASE)


def _query_kind(query: str) -> Optional[str]:
    """None = a drilling-archive question; 'greeting' | 'off_topic' otherwise."""
    q = (query or "").strip()
    if not q or _GREETING_RE.match(q):
        return "greeting"
    if _WELL_ID_RE.search(q) or re.search(r"\b\d{3,5}\s*(m|ft|md|tvd)?\b", q, re.IGNORECASE):
        return None  # a well id or a depth-like number: archive question even without keywords
    tokens = {t.lower() for t in re.findall(r"[A-Za-z]+", q)}
    return None if tokens & _DRILLING_LEXICON else "off_topic"


# Hindi example prompt shown FIRST in the Hindi-led guard line. Latin-script drilling words keep it an
# archive question for _query_kind (girujan/stuck/pipe are in the lexicon).
HINDI_EXAMPLE = "Girujan में stuck pipe कैसे छुड़ाया?"


def _guard_answer(kind: str, lang: Optional[str] = None) -> Answer:
    """Fixed scope-guard reply. Both languages are always present (same content); `lang` only
    decides which leads: None/"en" = English first (the historical default), "hi" = Hindi first
    with the Hindi example prompt ahead of the English examples (UI language toggle, 26 Sep)."""
    examples = " · ".join(f"\"{q}\"" for q in EXAMPLE_QUESTIONS[:3])
    if kind == "greeting":
        en = ("Hello. I am the NWIS archive assistant for the drilling desk. I answer questions from the "
              f"offset-well reports and cite the page for every claim. Try: {examples}.")
        hi_latn = "Namaste. Main sirf drilling archive ke sawalon ka jawab deta hoon, har jawab ke saath report ka page."
        hi = ("नमस्ते। मैं ड्रिलिंग डेस्क का NWIS आर्काइव सहायक हूँ। ऑफ़सेट वेल की रिपोर्ट से जवाब देता हूँ और "
              f"हर बात के साथ रिपोर्ट का पेज बताता हूँ। पूछकर देखें: \"{HINDI_EXAMPLE}\" · {examples}.")
    else:
        en = ("I only answer questions about the drilling archive (offset-well events, depths, formations, "
              f"remedies, NPT). Try: {examples}.")
        hi_latn = "Main sirf drilling archive ke sawalon ka jawab deta hoon, jaise: Girujan me stuck pipe kaise chhudaya?"
        hi = ("मैं सिर्फ़ ड्रिलिंग आर्काइव के सवालों का जवाब देता हूँ (ऑफ़सेट वेल के इवेंट, डेप्थ, फ़ॉर्मेशन, "
              f"उपाय, NPT)। जैसे: \"{HINDI_EXAMPLE}\" · {examples}.")
    if lang == "hi":
        english_short = en.split(" Try:")[0]  # examples already listed once in the Hindi line
        text = f"{hi}\n{english_short}"
    else:
        text = f"{en}\n{hi_latn}"  # unchanged default (lang absent) == "en"
    return Answer(text=text, citations=[], structured=[], refused=False, degraded=False,
                  guard=kind, suggestions=list(EXAMPLE_QUESTIONS))


_INCIDENT_LINE_RE = re.compile(r"^([A-Z]{3}-\d{3}|15[/_]9-F-[\w-]+)\s*,\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^-\[]+?)\s*-\s*(.+)$")


def _friendly(text: str) -> str:
    """Readable incident lines: 'WELL · depth · formation · NPT — action [tag]'."""
    out = []
    for line in text.splitlines():
        m = _INCIDENT_LINE_RE.match(line.strip())
        if m:
            well, depth, fm, npt, rest = (g.strip() for g in m.groups())
            out.append(f"{well} · {depth} · {fm} · {npt} — {rest}")
        else:
            out.append(line)
    return "\n".join(out)


def _document_id_lookup() -> dict[str, str]:
    """(well_id, page_ref) -> document_id, from the chunks table (chunks and
    events share page_ref because both come from the same ingest chunk)."""
    return {(c.well_id, c.page_ref): c.document_id for c in db.all_chunks()}


def _format_structured(events: list[db.EventRow]) -> str:
    lines = []
    for e in events:
        bits = [f"well={e.well_id}", f"event_type={e.event_type}"]
        if e.depth_md_m is not None:
            bits.append(f"depth_md_m={e.depth_md_m}")
        if e.formation:
            bits.append(f"formation={e.formation}")
        if e.hours_lost_npt is not None:
            bits.append(f"hours_lost_npt={e.hours_lost_npt}")
        if e.cause:
            bits.append(f"cause={e.cause!r}")
        if e.remedy:
            bits.append(f"remedy={e.remedy!r}")
        bits.append(f"page_ref={e.source_page_ref}")
        lines.append("- " + ", ".join(bits))
    return "\n".join(lines) if lines else "(none)"


def _format_quotes(hits: list[Hit]) -> str:
    lines = [f'[{h.well_id} | {h.page_ref}]: "{h.text}"' for h in hits]
    return "\n".join(lines) if lines else "(none)"


_PREAMBLE_RE = re.compile(r"^(based on (the )?(provided|given|available) (information|evidence|data)[^.\n:]*[.:]?\s*)",
                          re.IGNORECASE)


def _scrub(raw: str) -> str:
    """Deterministic clean-up of small-model habits: markdown headings/bold/bullets, numbered
    lists, 'Based on the provided information' preambles, blank-line runs; cap at 8 lines.
    Citation tags are left untouched."""
    text = raw.strip()
    if "INSUFFICIENT_EVIDENCE" in text.upper():
        return text
    text = _PREAMBLE_RE.sub("", text)
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^#{1,6}\s*", "", s)                 # headings
        s = re.sub(r"^(\d+[.)]|[-*•])\s+", "", s)         # list markers
        s = s.replace("**", "").replace("__", "")
        if re.fullmatch(r"(summary|summaries|events?|details?)[:.]?", s, re.IGNORECASE):
            continue
        out.append(s)
    # merge "Key: value" fragments the model may still emit into one line per incident
    merged: list[str] = []
    for s in out:
        if merged and re.match(r"^(event type|depth|formation|hours lost|cause|remedy|page reference)\b", s, re.IGNORECASE):
            merged[-1] = merged[-1] + "; " + s
        else:
            merged.append(s)
    return "\n".join(merged[:8]).strip()


def _strip_hallucinated_citations(text: str, valid_tags: set[tuple[str, str]]) -> tuple[str, int]:
    """Remove any [well_id | page_ref] tag not in valid_tags. Returns (clean_text,
    n_valid_tags_remaining)."""
    n_valid = 0

    def _sub(m: re.Match) -> str:
        nonlocal n_valid
        tag = (m.group(1).strip(), m.group(2).strip())
        if tag in valid_tags:
            n_valid += 1
            return m.group(0)
        return ""

    clean = CITATION_RE.sub(_sub, text)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip(), n_valid


def answer(query: str, filters: dict | None = None, lang: Optional[str] = None) -> Answer:
    """`lang` ("en" | "hi" | None) only affects the fixed scope-guard line; archive answers are
    always English because the reports are."""
    kind = _query_kind(query)
    if kind is not None:
        # Vector search always returns *some* nearest neighbour, so "hits exist" is no evidence
        # of relevance; the lexicon/well-id/depth check decides scope.
        return _guard_answer(kind, lang)
    hits = hybrid_search(query, k=8, filters=filters)
    events = structured_hits(query, filters=filters)

    if not hits and not events:
        return Answer(
            text="No evidence found for this query in the current knowledge base. "
                 "Cannot answer without at least one matching report chunk or event.",
            citations=[], structured=[], refused=True, degraded=False,
        )

    # Keep the evidence short: qwen2.5:7b mirrors whatever layout it is shown and lists
    # everything it is given (26 Sep). Top incidents by NPT, quotes trimmed.
    top_events = sorted(events, key=lambda e: (e.hours_lost_npt or 0.0), reverse=True)[:6]
    from dataclasses import replace as _dc_replace
    top_hits = [_dc_replace(h, text=h.text[:320].strip()) for h in hits[:4]]
    user_prompt = (
        f"QUESTION: {query}\n\n"
        f"STRUCTURED EVENTS:\n{_format_structured(top_events)}\n\n"
        f"QUOTES (cite EXACTLY as [well_id | page_ref]):\n{_format_quotes(top_hits)}\n\n"
        "REMINDER: plain text, first line = direct answer, then at most 5 incident lines. "
        "No markdown, no preamble. The evidence above is sufficient whenever any event or quote "
        "relates to the question."
    )

    if not llm_mod.available():
        return _offline_answer(query, events, hits)
    try:
        raw = llm_mod.complete(SYSTEM_PROMPT, user_prompt, temperature=0.1, max_tokens=400)
    except Exception:  # noqa: BLE001 -- server vanished / timed out: degrade, never 500
        return _offline_answer(query, events, hits)
    raw = _scrub(raw or "")

    if REFUSAL_MARKER in raw.upper() or not raw:
        # The model would not write a narrative, but retrieval DID find matching incidents.
        # Show them as a structured, fully-cited fallback instead of a bare refusal
        # (26 Sep: "Barail kick mud weight" refused with 28 matching kick incidents in the DB).
        if events or hits:
            return Answer(
                text=_structured_fallback_text(query, events, hits),
                citations=_citations_from_hits(hits[:5]),
                structured=[e.model_dump() for e in events], refused=False, degraded=True,
            )
        return Answer(
            text="Evidence retrieved was insufficient to answer with citations; refusing "
                 "rather than guessing.",
            citations=[], structured=[e.model_dump() for e in events], refused=True, degraded=False,
        )

    # A tag is valid if it names a retrieved quote OR a structured event row the model was
    # shown (26 Sep: event-row tags were being stripped as hallucinations, which forced the
    # auto-attached-sources path with the wrong wells).
    hit_by_tag = {(h.well_id, h.page_ref): h for h in hits}
    ev_by_tag = {(e.well_id, e.source_page_ref): e for e in top_events}
    valid_tags = set(hit_by_tag) | set(ev_by_tag)
    clean_text, n_valid = _strip_hallucinated_citations(raw, valid_tags)
    degraded = n_valid == 0

    doc_lookup = _document_id_lookup()
    cited_tags = {
        (m.group(1).strip(), m.group(2).strip())
        for m in CITATION_RE.finditer(clean_text)
    }
    citations = []
    for (well_id, page_ref) in cited_tags:
        if (well_id, page_ref) in hit_by_tag:
            quote = hit_by_tag[(well_id, page_ref)].text
            doc_id = doc_lookup.get((well_id, page_ref), "")
        elif (well_id, page_ref) in ev_by_tag:
            e = ev_by_tag[(well_id, page_ref)]
            quote = e.free_text or " — ".join(x for x in (e.cause, e.remedy) if x)
            doc_id = e.source_document_id
        else:
            continue
        citations.append(Citation(well_id=well_id, document_id=doc_id, page_ref=page_ref, quote=quote))

    if degraded and hits:
        # The narrative carried no valid tag. The evidence it was written from is still the
        # retrieved hits, so attach them explicitly rather than returning an uncited answer.
        citations = _citations_from_hits(hits[:5])
        sources = ", ".join(f"[{c.well_id} | {c.page_ref}]" for c in citations)
        clean_text = clean_text.rstrip() + "\n\nSources (auto-attached from retrieved evidence): " + sources

    return Answer(
        text=_friendly(clean_text),
        citations=citations,
        structured=[e.model_dump() for e in events],
        refused=False,
        degraded=degraded,
        suggestions=[q for q in EXAMPLE_QUESTIONS if q.lower() != query.lower()][:3],
    )


OFFLINE_PREFIX = "Answer engine offline on this deployment; showing matching incidents from the archive."


def _offline_answer(query: str, events: list[db.EventRow], hits: list[Hit]) -> Answer:
    body = _structured_fallback_text(query, events, hits)
    # drop the 'No narrative answer ...' header line; the offline prefix replaces it
    body_lines = body.splitlines()[1:]
    return Answer(
        text="\n".join([OFFLINE_PREFIX] + body_lines),
        citations=_citations_from_hits(hits[:5]),
        structured=[e.model_dump() for e in events], refused=False, degraded=True,
    )


def _citations_from_hits(hits: list[Hit]) -> list[Citation]:
    doc_lookup = _document_id_lookup()
    seen: set[tuple[str, str]] = set()
    out: list[Citation] = []
    for h in hits:
        key = (h.well_id, h.page_ref)
        if key in seen:
            continue
        seen.add(key)
        out.append(Citation(well_id=h.well_id, document_id=doc_lookup.get(key, ""),
                            page_ref=h.page_ref, quote=h.text))
    return out


def _structured_fallback_text(query: str, events: list[db.EventRow], hits: list[Hit]) -> str:
    lines = [f"No narrative answer for \"{query}\"; showing the matching incidents from the archive instead."]
    # one line per incident: same well/type/depth(±25 m) collapses, row with a remedy wins
    uniq: list[db.EventRow] = []
    for e in sorted(events, key=lambda e: ((e.hours_lost_npt or 0), bool(e.remedy)), reverse=True):
        dup = next((u for u in uniq if u.well_id == e.well_id and u.event_type == e.event_type
                    and u.depth_md_m is not None and e.depth_md_m is not None
                    and abs(u.depth_md_m - e.depth_md_m) <= 25), None)
        if dup is None:
            uniq.append(e)
    for e in uniq[:6]:
        depth = f"{e.depth_md_m:.0f} m MD" if e.depth_md_m is not None else "depth n/a"
        npt = f", {e.hours_lost_npt:.1f} h NPT" if e.hours_lost_npt else ""
        mw = f", MW {e.mud_weight_ppg_at_event:.1f} ppg" if e.mud_weight_ppg_at_event else ""
        lines.append(f"- {e.well_id} | {e.source_page_ref}: {e.event_type} at {depth} ({e.formation or 'formation n/a'}{npt}{mw})"
                     + (f" — {e.remedy}" if e.remedy else ""))
    if not events:
        for h in hits[:4]:
            lines.append(f"- [{h.well_id} | {h.page_ref}] {h.text[:160].strip()}")
    return "\n".join(lines)
