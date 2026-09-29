"""Alert -> recommendation, always cited.

Preferred path: nwis.search.answer.answer() (Phase 4, cited RAG over ingested DDRs).
Fallback (search unavailable, or it returns no citation -- anti-hallucination: never
show an answer without a citation): a template built from the precedent events'
`remedy` fields (most common remedy phrase). If there is truly nothing to cite from,
the live sensor evidence itself is cited so the Alert.citations field is never empty.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from nwis.schema import Alert


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_query(alert: Alert) -> str:
    formation = alert.formation or "the current formation"
    return f"{alert.hazard.value} in {formation} near {alert.well_id}"


def _format_citation(c) -> str:
    """Handles both a search-answer Citation dataclass and a geo-correlate offset
    event dict -- whichever shape the caller hands us."""
    well = _get(c, "well_id") or _get(c, "source_well_id")
    doc = _get(c, "document_id")
    page = _get(c, "page_ref") or _get(c, "source_page_ref")
    label = " | ".join(str(p) for p in (well, doc, page) if p)
    if not label:
        label = str(_get(c, "event_id") or c)
    quote = _get(c, "quote")
    return f"{label} — \"{quote}\"" if quote else label


def _template_from_precedents(alert: Alert, precedent_events: list) -> tuple[str, list[str]]:
    remedies = [r for ev in precedent_events if (r := _get(ev, "remedy"))]
    citations: list[str] = []
    for ev in precedent_events:
        cite = _get(ev, "page_ref") or _get(ev, "source_page_ref") or _get(ev, "event_id")
        if cite:
            citations.append(str(cite))

    if remedies:
        top_remedy, _count = Counter(remedies).most_common(1)[0]
        rec = f"Offset-well precedent for {alert.hazard.value}: {top_remedy}"
    else:
        rec = (
            f"No offset remedy text available yet for {alert.hazard.value}; "
            f"follow standard SOP and monitor closely."
        )
    if not citations:
        citations = [f"{alert.well_id} live sensors @ t={alert.t_s:.0f}s (no document precedent)"]
    return rec, citations


RECO_SYSTEM = (
    "You are the senior drilling engineer on duty at Oil India's eRTMAC, Duliajan. A lookahead "
    "alert has fired on the active well. Using ONLY the offset-well precedents and report lines "
    "given, write an operational recommendation for the driller: at most 2 sentences, imperative "
    "voice, concrete (mud weight, pill, trip practice, monitoring), no preamble, no headings, no "
    "bullet lists. End with the citation tags of the precedents you relied on, exactly in the "
    "form [well_id | page_ref]. If the precedents contain no usable remedy, reply with the single "
    "token NO_RECOMMENDATION."
)


def _llm_recommendation(alert: Alert, precedent_events: list, query: str) -> tuple[Optional[str], list[str]]:
    import re

    from nwis import llm as llm_mod
    from nwis.search.retrieve import hybrid_search

    lines: list[str] = []
    valid: dict[str, str] = {}
    for ev in precedent_events[:8]:
        well = _get(ev, "well_id") or _get(ev, "source_well_id")
        page = _get(ev, "page_ref") or _get(ev, "source_page_ref")
        depth = _get(ev, "depth_md_m") or _get(ev, "md") or _get(ev, "tvd")
        parts = [f"{well} | {page}", f"{_get(ev, 'event_type', alert.hazard.value)}"]
        if depth is not None:
            parts.append(f"@ {float(depth):.0f} m")
        for k, lbl in (("formation", ""), ("hours_lost_npt", "h NPT"), ("mud_weight_ppg_at_event", "ppg MW"),
                       ("cause", "cause:"), ("remedy", "remedy:")):
            v = _get(ev, k)
            if v not in (None, ""):
                parts.append(f"{lbl} {v}".strip() if lbl.endswith(":") else f"{v} {lbl}".strip())
        lines.append("- " + " | ".join(parts))
        if well and page:
            valid[f"{well} | {page}"] = f"{well} | {page}"
    hits = []
    try:
        hits = hybrid_search(query, k=4, filters={"event_type": alert.hazard.value,
                                                   "formation": alert.formation} if alert.formation
                             else {"event_type": alert.hazard.value})
    except Exception:
        hits = []
    for h in hits:
        tag = f"{h.well_id} | {h.page_ref}"
        valid[tag] = tag
        lines.append(f"- {tag} | report line: {h.text[:220].strip()}")
    if not lines:
        return None, []

    user = (f"ACTIVE WELL: {alert.well_id}, bit at {alert.depth_md_m:.0f} m MD, "
            f"formation {alert.formation or 'unknown'}, hazard ahead: {alert.hazard.value}.\n"
            f"ALERT: {alert.message}\n\nPRECEDENTS:\n" + "\n".join(lines))
    raw = (llm_mod.complete(RECO_SYSTEM, user, temperature=0.1, max_tokens=220) or "").strip()
    if not raw or "NO_RECOMMENDATION" in raw.upper():
        return None, []
    tags = [m.group(0) for m in re.finditer(r"\[([^\[\]|]+)\|([^\[\]]+)\]", raw)]
    used = [t.strip("[]").strip() for t in tags]
    used = [" | ".join(p.strip() for p in u.split("|", 1)) for u in used]
    cites = [u for u in used if u in valid]
    if not cites:
        # model wrote advice but no valid tag: cite the precedents it was given (never uncited)
        cites = list(valid)[:4]
    text = re.sub(r"\s*\[[^\[\]]+\|[^\[\]]+\]", "", raw).strip()
    if not text:
        return None, []
    return text, cites


def recommend_for_alert(alert: Alert, precedent_events: Optional[list] = None) -> Alert:
    """Fill alert.recommendation + alert.citations in place and return it."""
    precedent_events = precedent_events or []
    query = build_query(alert)

    try:
        from nwis.search.answer import answer as search_answer
    except ImportError:
        search_answer = None

    # Preferred path: a short, driller-facing recommendation written from the precedent
    # incidents (cause/remedy/MW/NPT) plus the top retrieved DDR lines. The general Q&A
    # prompt produced report summaries instead of advice (26 Sep), hence a dedicated prompt.
    from nwis import llm as _llm

    if search_answer is not None and _llm.available():
        try:
            text, cites = _llm_recommendation(alert, precedent_events, query)
            if text and cites:
                alert.recommendation = text
                alert.citations = cites
                return alert
        except Exception:
            pass  # degrade to the template below

    rec, citations = _template_from_precedents(alert, precedent_events)
    alert.recommendation = rec
    alert.citations = citations
    return alert
