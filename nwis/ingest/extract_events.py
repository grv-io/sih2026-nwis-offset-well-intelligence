"""Chunk text -> list[LLMEvent] via nwis.llm.extract, with a hallucination guard
and a blended confidence score.

Fallback note: instructor's Ollama JSON mode (instructor.Mode.JSON against the
OpenAI-compatible /v1 endpoint) occasionally returns output that fails Pydantic
validation even after instructor's own retries (qwen2.5:7b sometimes wraps JSON in
prose or drops required keys). When `nwis.llm.extract` raises, we fall back to
plain-JSON prompting via `nwis.llm.complete` + `ExtractedEvents.model_validate_json`,
with one corrective retry.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from rapidfuzz import fuzz

from nwis import llm as llm_mod
from nwis.ingest.chunk import Chunk
from nwis.ingest.normalise import canonical_formation
from nwis.schema import ExtractedEvents, LLMEvent

QUOTE_MATCH_THRESHOLD = 80

SYSTEM_PROMPT = """You are a senior drilling engineer on the eRTMAC (e-Real Time Monitoring \
and Control) desk at Oil India Limited, Duliajan. You read Daily Drilling Report (DDR) and Well \
Completion Report (WCR) narrative excerpts and extract ONLY genuine operational EVENTS that \
interrupted, threatened, or added non-productive time to normal drilling — using a FIXED taxonomy. \
Never invent, infer beyond the text, or exaggerate. If nothing eventful happened in the excerpt, \
return an empty events list.

Fixed taxonomy (event_type — one-line definition):
- mud_loss: loss of drilling fluid into the formation beyond planned/expected losses (lost circulation).
- kick: uncontrolled influx of formation fluid (gas/oil/water) into the wellbore.
- stuck_pipe: drillstring could not rotate or move freely and required remedial action (jarring, spotting a pill, working free).
- overpressure: formation/annular pressure explicitly noted higher than expected/planned (e.g. gas-cut mud, unexplained high SPP).
- torque_spike: a sudden abnormal rise in torque flagged as a problem (not routine/expected torque).
- cementing_issue: a problem during or after cementing (channelling, float failure, poor bond, losses while cementing).
- fishing_operation: retrieval of junk, parted string, or dropped downhole equipment.
- wellbore_instability: hole collapse, sloughing/caving shale, tight hole.
- gas_show: an elevated gas reading explicitly called out as a show/event (not routine background gas).
- twist_off: the drillstring parted / twisted off.
- lost_bha: the bottom-hole assembly (or part of it) was lost / left in the hole.
- npt_other: any other non-productive-time event that clearly halted operations but doesn't fit above (e.g. equipment breakdown, weather NPT) — use sparingly.

Do NOT extract: routine drilling progress, routine tripping in/out, routine casing running with no \
problems, routine surveys, routine mud checks/circulating, rig maintenance with no NPT, meetings, or \
BOP tests that pass. These are normal operations, not events.

For each real event you find, fill:
- depth_md_m: measured depth in metres, only if stated (leave null if only feet are given and you are unsure of the conversion).
- formation: copy the formation name EXACTLY as written in the excerpt (do not correct the spelling).
- quote: the exact verbatim sentence(s) copied from the excerpt that support this event — must literally appear in the input, never a paraphrase.
- confidence: your own 0-1 confidence this is a real event, correctly classified.
- cause / remedy / severity / hours_lost_npt / mud_weight_ppg_at_event: fill only if stated in the excerpt, else leave null/default.

Return {"events": []} when nothing eventful happened in the excerpt.

--- Example 1 ---
Report excerpt:
11-Mar-2019: Drilling 12-1/4" hole 2410-2465m in Girujan clay. 1430 hrs: while tripping out for bit \
change, string got stuck at 2460m, unable to work free. POOH torque increased sharply. Spotted 15 bbl \
pipe-freeing pill, worked string free after 3 hrs. Resumed tripping.

Correct output:
{"events": [{"event_type": "stuck_pipe", "depth_md_m": 2460, "interval_top_md_m": null, "interval_base_md_m": null, "formation": "Girujan clay", "severity": "medium", "hours_lost_npt": 3, "cause": "String got stuck while tripping out for bit change, unable to work free", "remedy": "Spotted 15 bbl pipe-freeing pill, worked string free after 3 hrs", "mud_weight_ppg_at_event": null, "quote": "while tripping out for bit change, string got stuck at 2460m, unable to work free.", "confidence": 0.9}]}

--- Example 2 ---
Report excerpt:
14-Mar-2019: Drilling ahead 2510-2565m, no problems encountered. Circulated bottoms up, samples \
normal, no gas. POOH, RIH, resumed drilling. NPT nil.

Correct output:
{"events": []}

--- Example 3 (terse rig shorthand — still events) ---
Report excerpt:
Drilled 8 1/2" pilot hole f/ 467 m to 473 m, 2272 lpm, 116 bar. Observed gain of 1 m3 in active pit, \
flow checked, well static after 15 min, continued drilling. POOH w/ BHA, tightspot f/ 1348 m t/ 1330 m, \
worked string/backreamed, max overpull 30 MT. Static losses approx 100 l/hr while lining up on trip tank.

Correct output:
{"events": [{"event_type": "kick", "depth_md_m": 473, "interval_top_md_m": 467, "interval_base_md_m": 473, "formation": null, "severity": "medium", "hours_lost_npt": null, "cause": "Gain of 1 m3 in active pit while drilling", "remedy": "Flow checked, well static after 15 min", "mud_weight_ppg_at_event": null, "quote": "Observed gain of 1 m3 in active pit, flow checked, well static after 15 min", "confidence": 0.8}, {"event_type": "wellbore_instability", "depth_md_m": 1348, "interval_top_md_m": 1330, "interval_base_md_m": 1348, "formation": null, "severity": "medium", "hours_lost_npt": null, "cause": "Tight spot while pulling out of hole with BHA", "remedy": "Worked string and backreamed, max overpull 30 MT", "mud_weight_ppg_at_event": null, "quote": "tightspot f/ 1348 m t/ 1330 m, worked string/backreamed, max overpull 30 MT", "confidence": 0.8}, {"event_type": "mud_loss", "depth_md_m": null, "interval_top_md_m": null, "interval_base_md_m": null, "formation": null, "severity": "low", "hours_lost_npt": null, "cause": "Static losses approx 100 l/hr", "remedy": null, "mud_weight_ppg_at_event": null, "quote": "Static losses approx 100 l/hr while lining up on trip tank", "confidence": 0.7}]}
--- End examples ---
"""


def _build_user_prompt(chunk: Chunk, well_context: Optional[dict]) -> str:
    ctx_lines = [f"{k}: {v}" for k, v in (well_context or {}).items() if v is not None]
    ctx = "\n".join(ctx_lines)
    header = f"Well context:\n{ctx}\n\n" if ctx else ""
    return f"{header}Report excerpt (page_ref={chunk.page_ref}):\n\n{chunk.text}"


def _verify_quote(quote: str, chunk_text: str) -> bool:
    if not quote or not quote.strip():
        return False
    return fuzz.partial_ratio(quote, chunk_text) >= QUOTE_MATCH_THRESHOLD


def _score_confidence(ev: LLMEvent, quote_verified: bool) -> float:
    depth_present = ev.depth_md_m is not None
    formation_resolvable = canonical_formation(ev.formation) is not None
    return (
        0.5 * ev.confidence
        + 0.2 * (1.0 if depth_present else 0.0)
        + 0.15 * (1.0 if formation_resolvable else 0.0)
        + 0.15 * (1.0 if quote_verified else 0.0)
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _fallback_json_extract(user: str) -> ExtractedEvents:
    """Plain-JSON-prompting fallback with one corrective retry."""
    schema_hint = json.dumps(ExtractedEvents.model_json_schema())
    sys2 = (
        SYSTEM_PROMPT
        + "\n\nRespond with ONLY a single JSON object matching this JSON schema — "
          "no markdown code fences, no commentary, no extra keys:\n" + schema_hint
    )
    last_err: Exception | None = None
    for attempt in range(2):
        raw = llm_mod.complete(sys2, user, temperature=0.0, max_tokens=1500)
        cleaned = _strip_fences(raw)
        try:
            return ExtractedEvents.model_validate_json(cleaned)
        except Exception as e:  # noqa: BLE001
            last_err = e
            user = (
                user
                + "\n\n(Your previous reply was not valid JSON matching the schema. "
                  "Reply again with ONLY valid JSON, nothing else.)"
            )
    raise RuntimeError(f"fallback JSON extraction failed twice: {last_err}")


def extract_from_chunk(chunk: Chunk, well_context: Optional[dict] = None) -> list[LLMEvent]:
    """Run structured extraction on one chunk, drop hallucinated (unverifiable-quote)
    events, and blend each surviving event's confidence.
    """
    user = _build_user_prompt(chunk, well_context)
    try:
        result: ExtractedEvents = llm_mod.extract(ExtractedEvents, SYSTEM_PROMPT, user)
    except Exception:  # noqa: BLE001 — instructor/Ollama JSON mode misbehaving
        result = _fallback_json_extract(user)

    kept: list[LLMEvent] = []
    for ev in result.events:
        verified = _verify_quote(ev.quote, chunk.text)
        if not verified:
            continue  # hallucination guard: no supporting quote found in the source text
        ev.confidence = _score_confidence(ev, verified)
        kept.append(ev)
    return kept
