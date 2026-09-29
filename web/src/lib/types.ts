// Shapes returned by the FastAPI backend (api/*.py). Kept loose where another
// phase owns the contract (risk, live) so a field rename degrades, not crashes.

export type Severity = 'low' | 'medium' | 'high' | 'critical'
export type Source = 'extracted' | 'truth' | 'all'
export type Basin = 'assam' | 'volve'
export type HazardFamily = 'losses' | 'stuck' | 'well_control' | 'other'

export interface Well {
  well_id: string
  name: string
  field: string
  lat: number
  lon: number
  kb_elev_m?: number
  spud_date?: string | null
  td_md_m: number
  trajectory_type: string
  status?: string
  basin?: string
}

export interface MapWell extends Well {
  event_counts: Record<string, number>
  n_events: number
  dominant_event_type: string | null
  dominant_family: HazardFamily | null
}

export interface OffsetCandidate {
  well_id: string
  score: number
  distance_km: number
  bearing_deg: number
  dimensions: Record<string, number>
  weights_used: Record<string, number>
  dimensions_unavailable: string[]
}

export interface MapData {
  active_well_id: string | null
  radius_km: number
  source: Source
  wells: MapWell[]
  candidates: OffsetCandidate[]
  ring: [number, number][]
  /** Set (and candidates == []) when the basin has no formation tops to rank offsets with (Volve). */
  note?: string
}

export interface DrillingEvent {
  event_id: string
  well_id: string
  source_document_id: string
  source_page_ref: string
  report_date: string | null
  depth_md_m: number | null
  depth_tvd_m: number | null
  formation: string | null
  formation_offset_from_top_m?: number | null
  event_type: string
  severity: Severity
  hours_lost_npt: number | null
  cause: string | null
  remedy: string | null
  mud_weight_ppg_at_event?: number | null
  free_text: string
  extraction_confidence: number
  extraction_method: string
  reviewed_by_human: boolean
  is_ground_truth?: boolean
  reason?: string
}

export interface ReviewQueue {
  threshold: number
  counters: { extracted: number; reviewed: number; pending: number }
  items: DrillingEvent[]
}

export interface Summary {
  wells: number
  events_truth: number
  events_extracted: number
  documents: number
  documents_ingested: number
  chunks: number
  formations: string[]
  event_types: string[]
}

export interface Health {
  status: string
  llm: { ollama: boolean; chat_model_present?: boolean; embed_model_present?: boolean; provider?: string; error?: string }
}

export interface SearchHit {
  chunk_id: string
  well_id: string
  page_ref: string
  text: string
  score: number
  why: string
  document_id?: string
}

export interface AnswerCitation {
  well_id: string
  document_id: string
  page_ref: string
  quote: string
}

export interface Answer {
  text: string
  citations: AnswerCitation[]
  structured: Partial<DrillingEvent>[]
  refused: boolean
  degraded: boolean
  /** 'greeting' | 'off_topic' when the scope guard answered with its fixed line */
  guard?: string | null
  suggestions?: string[]
}

export interface DocumentText {
  document_id: string
  well_id: string
  doc_type: string
  report_date: string | null
  path: string
  file_name: string
  is_scanned: boolean
  n_pages: number
  page: number
  text_source: string
  text: string
}

export interface DipFit {
  formation: string
  theta_deg: number
  azimuth_deg: number
  rmse_m: number | null
  n: number
}

export interface LookaheadHit {
  source_well_id: string
  event_id: string
  event_type: string
  severity: Severity
  formation: string | null
  expected_md_m: number
  expected_tvd_m: number
  page_ref: string
  cause: string | null
  remedy: string | null
  hours_lost: number | null
  dip_method: string
  dip_n_wells: number
}

export interface RiskInterval {
  well_id?: string
  top_md_m: number
  base_md_m: number
  formation?: string | null
  hazard: string
  score: number
  precedent_component: number
  anomaly_component: number
  model_component: number
  top_reasons: string[]
  mode?: 'supervised' | 'indicator'
}

export interface RiskView {
  mode: 'supervised' | 'indicator' | 'mixed'
  origin: 'model' | 'precedent-fallback'
  intervals: RiskInterval[]
  note?: string
  /** Static demo only: the recorded bit depth actually shown (nearest to the one asked for). */
  staticMd?: number
}

export interface LiveSample {
  t_s: number
  depth_md_m: number
  wob_klbf?: number
  rpm?: number
  torque_kftlb?: number
  rop_m_hr?: number
  spp_psi?: number
  flow_in_gpm?: number
  flow_out_gpm?: number
  pit_vol_bbl?: number
  mw_ppg?: number
  gas_pct?: number
}

export interface Alert {
  alert_id: string
  well_id: string
  t_s: number
  depth_md_m: number
  formation: string | null
  rule: string
  hazard: string
  severity: Severity
  message: string
  precedent_event_ids: string[]
  recommendation: string | null
  citations: string[]
  acknowledged: boolean
}

export interface LiveState {
  well_id: string
  speed: number
  running: boolean
  paused: boolean
  finished: boolean
  progress: number
  md_range: [number, number] | null
  t_s: number | null
  depth_md_m: number | null
  formation: string | null
  last_sample: LiveSample | null
  window: LiveSample[]
  n_alerts_open: number
  signals_unavailable: string[]
}
