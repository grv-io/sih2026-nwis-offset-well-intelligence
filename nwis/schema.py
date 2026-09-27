"""Canonical data models for NWIS. Every module speaks these types.

Frozen on 26 Sep 2026. Change only via docs/PROJECT_LOG.md decision entry.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class EventType(str, Enum):
    mud_loss = "mud_loss"
    kick = "kick"
    stuck_pipe = "stuck_pipe"
    overpressure = "overpressure"
    torque_spike = "torque_spike"
    cementing_issue = "cementing_issue"
    fishing_operation = "fishing_operation"
    wellbore_instability = "wellbore_instability"
    gas_show = "gas_show"
    twist_off = "twist_off"
    lost_bha = "lost_bha"
    npt_other = "npt_other"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ExtractionMethod(str, Enum):
    ocr_llm_local = "ocr_llm_local"
    ocr_llm_api = "ocr_llm_api"
    human_reviewed = "human_reviewed"
    synthetic_truth = "synthetic_truth"


class TrajectoryType(str, Enum):
    vertical = "vertical"
    deviated = "deviated"
    horizontal = "horizontal"


# Upper Assam stratigraphic column, youngest -> oldest.
# Sources: Mandal & Dasgupta (OIL, SPG 2013); USGS Bulletin 2208-D (2004). See research/03.
FORMATIONS: list[str] = [
    "Alluvium",
    "Dhekiajuli",
    "Namsang",
    "Girujan",
    "Tipam",
    "Barail",
    "Kopili",
    "Sylhet",
    "Basement",
]

# Spelling variants seen in reports -> canonical. Extend freely; used by ingest/normalise.
FORMATION_ALIASES: dict[str, str] = {
    "gurjan": "Girujan",
    "girujan clay": "Girujan",
    "girujan fm": "Girujan",
    "tipam ss": "Tipam",
    "tipam sandstone": "Tipam",
    "tipam sst": "Tipam",
    "barail gp": "Barail",
    "barail group": "Barail",
    "barail coal shale": "Barail",
    "barail main sand": "Barail",
    "kopili sh": "Kopili",
    "kopili shale": "Kopili",
    "sylhet lst": "Sylhet",
    "sylhet limestone": "Sylhet",
    "namsang fm": "Namsang",
    "dhekiajuli fm": "Dhekiajuli",
    "siwalik": "Dhekiajuli",
    "alluvium": "Alluvium",
    "basement": "Basement",
}


# --------------------------------------------------------------------------- #
# Core entities
# --------------------------------------------------------------------------- #
class Well(BaseModel):
    well_id: str = Field(..., description="Unique id, e.g. 'DUL-017'")
    name: str
    field: str = Field(..., description="e.g. Duliajan, Moran, Naharkatiya")
    lat: float
    lon: float
    kb_elev_m: float = Field(0.0, description="Kelly bushing elevation above MSL")
    spud_date: Optional[date] = None
    td_md_m: float = Field(..., description="Total depth, measured")
    trajectory_type: TrajectoryType = TrajectoryType.vertical
    status: str = "completed"
    basin: str = "Upper Assam"


class FormationTop(BaseModel):
    well_id: str
    formation: str
    top_md_m: float
    top_tvd_m: float
    base_md_m: Optional[float] = None
    base_tvd_m: Optional[float] = None

    @field_validator("formation")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in FORMATIONS:
            raise ValueError(f"unknown formation {v!r}; canonical set is {FORMATIONS}")
        return v


class SurveyStation(BaseModel):
    """Directional survey station (MD, inclination, azimuth)."""
    well_id: str
    md_m: float
    inc_deg: float
    azi_deg: float


class SourceDocument(BaseModel):
    document_id: str
    well_id: str
    doc_type: str = Field(..., description="DDR | WCR | mud_log | other")
    path: str
    report_date: Optional[date] = None
    n_pages: int = 1
    is_scanned: bool = False


class DrillingEvent(BaseModel):
    """One operational event extracted from a report. The unit of institutional memory."""
    event_id: str
    well_id: str
    source_document_id: str
    source_page_ref: str = Field(..., description="e.g. 'DUL-017_DDR_2019-03-11.pdf#p2'")
    report_date: Optional[date] = None
    depth_md_m: Optional[float] = None
    depth_tvd_m: Optional[float] = None
    interval_top_md_m: Optional[float] = None
    interval_base_md_m: Optional[float] = None
    formation: Optional[str] = None
    formation_offset_from_top_m: Optional[float] = None
    event_type: EventType
    severity: Severity = Severity.medium
    hours_lost_npt: Optional[float] = None
    cause: Optional[str] = None
    remedy: Optional[str] = None
    mud_weight_ppg_at_event: Optional[float] = None
    ecd_ppg_at_event: Optional[float] = None
    free_text: str = ""
    extraction_confidence: float = Field(0.5, ge=0.0, le=1.0)
    extraction_method: ExtractionMethod = ExtractionMethod.ocr_llm_local
    reviewed_by_human: bool = False

    @field_validator("formation")
    @classmethod
    def _canonical(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v in FORMATIONS:
            return v
        key = v.strip().lower()
        if key in FORMATION_ALIASES:
            return FORMATION_ALIASES[key]
        raise ValueError(f"formation {v!r} not canonical; normalise first")


class ExtractedEvents(BaseModel):
    """What the LLM returns for one chunk. Ids/provenance are filled by the pipeline."""
    events: list["LLMEvent"]


class LLMEvent(BaseModel):
    """Loose, LLM-facing shape. Converted to DrillingEvent after validation."""
    event_type: EventType
    depth_md_m: Optional[float] = None
    interval_top_md_m: Optional[float] = None
    interval_base_md_m: Optional[float] = None
    formation: Optional[str] = Field(None, description="Formation name as written in the report")
    severity: Severity = Severity.medium
    hours_lost_npt: Optional[float] = None
    cause: Optional[str] = None
    remedy: Optional[str] = None
    mud_weight_ppg_at_event: Optional[float] = None
    quote: str = Field("", description="Verbatim sentence(s) from the report supporting this event")
    confidence: float = Field(0.5, ge=0.0, le=1.0)


# --------------------------------------------------------------------------- #
# Live / alerting
# --------------------------------------------------------------------------- #
class LiveSample(BaseModel):
    well_id: str
    t_s: float
    depth_md_m: float
    wob_klbf: float
    rpm: float
    torque_kftlb: float
    rop_m_hr: float
    spp_psi: float
    flow_in_gpm: float
    flow_out_gpm: float
    pit_vol_bbl: float
    mw_ppg: float
    gas_pct: float


class Alert(BaseModel):
    alert_id: str
    well_id: str
    t_s: float
    depth_md_m: float
    formation: Optional[str] = None
    rule: str = Field(..., description="precedent_zone | param_anomaly | model_risk")
    hazard: EventType
    severity: Severity
    message: str
    precedent_event_ids: list[str] = []
    recommendation: Optional[str] = None
    citations: list[str] = []
    acknowledged: bool = False


class RiskInterval(BaseModel):
    well_id: str
    top_md_m: float
    base_md_m: float
    formation: Optional[str] = None
    hazard: EventType
    score: float = Field(..., ge=0.0, le=1.0)
    precedent_component: float = 0.0
    anomaly_component: float = 0.0
    model_component: float = 0.0
    top_reasons: list[str] = []
