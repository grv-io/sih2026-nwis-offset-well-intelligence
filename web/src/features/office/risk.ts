import { ApiError, api } from '../../lib/api'
import type { LookaheadHit, RiskInterval, RiskView, Source } from '../../lib/types'

const INTERVAL_M = 50
const LOOKAHEAD_M = 200

/** Accept whatever shape the Phase 5 /risk router returns: an array of
 *  RiskInterval, or an object carrying {mode, intervals|risk|scores}. */
export function normaliseRisk(raw: unknown): RiskView | null {
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const list = (Array.isArray(raw) ? raw : (obj.intervals ?? obj.risk ?? obj.scores ?? obj.results)) as unknown
  if (!Array.isArray(list)) return null
  const intervals: RiskInterval[] = list
    .filter((r) => r && typeof r === 'object')
    .map((r) => {
      const x = r as Record<string, unknown>
      const reasons = Array.isArray(x.top_reasons) ? (x.top_reasons as unknown[]).map(String) : []
      const num = (k: string, d = 0) => (typeof x[k] === 'number' ? (x[k] as number) : d)
      return {
        well_id: x.well_id as string | undefined,
        top_md_m: num('top_md_m'),
        base_md_m: num('base_md_m', num('top_md_m') + INTERVAL_M),
        formation: (x.formation as string) ?? null,
        hazard: String(x.hazard ?? 'npt_other'),
        score: Math.max(0, Math.min(1, num('score'))),
        precedent_component: num('precedent_component'),
        anomaly_component: num('anomaly_component'),
        model_component: num('model_component'),
        top_reasons: reasons.filter((s) => !s.startsWith('mode:')),
        mode: (reasons.find((s) => s.startsWith('mode:'))?.slice(5) ?? (typeof x.mode === 'string' ? x.mode : undefined)) as RiskInterval['mode'],
      }
    })
  const topMode = typeof obj.mode === 'string' ? obj.mode : undefined
  const modes = new Set(intervals.map((i) => i.mode ?? topMode ?? (i.model_component > 0 ? 'supervised' : 'indicator')))
  return {
    mode: modes.size > 1 ? 'mixed' : ((modes.values().next().value ?? 'indicator') as RiskView['mode']),
    origin: 'model',
    intervals,
    note: typeof obj.note === 'string' ? obj.note : undefined,
  }
}

/** A8 floor: fixed-bin frequency count of dip-corrected offset precedent ahead of
 *  the bit (from /lookahead). Used when the risk model is not loaded. */
export function precedentFallback(hits: LookaheadHit[], md: number): RiskView {
  const bins = new Map<string, { top: number; hits: LookaheadHit[] }>()
  for (const h of hits) {
    const top = md + Math.floor((h.expected_md_m - md) / INTERVAL_M) * INTERVAL_M
    const key = `${top}|${h.event_type}`
    const b = bins.get(key) ?? { top, hits: [] }
    b.hits.push(h)
    bins.set(key, b)
  }
  const intervals: RiskInterval[] = [...bins.values()].map(({ top, hits: hs }) => {
    const wells = [...new Set(hs.map((h) => h.source_well_id))]
    const density = Math.min(1, hs.length / 3) // 3+ offset events in one 50 m bin saturates
    return {
      top_md_m: top,
      base_md_m: top + INTERVAL_M,
      formation: hs[0].formation,
      hazard: hs[0].event_type,
      score: density,
      precedent_component: density,
      anomaly_component: 0,
      model_component: 0,
      top_reasons: [
        `${hs.length} offset event${hs.length > 1 ? 's' : ''} in ${wells.length} well${wells.length > 1 ? 's' : ''} (${wells.join(', ')})`,
        ...hs.slice(0, 2).map((h) => `${h.source_well_id} · ${h.page_ref}`),
      ],
    }
  })
  intervals.sort((a, b) => a.top_md_m - b.top_md_m || b.score - a.score)
  return {
    mode: 'indicator',
    origin: 'precedent-fallback',
    intervals,
    note: 'Risk model not loaded: showing offset-precedent density only (fixed 50 m bins, dip-corrected).',
  }
}

export async function loadRisk(wellId: string, md: number, source: Source): Promise<RiskView> {
  try {
    const view = normaliseRisk(await api.risk(wellId, md))
    if (view) return view
  } catch (e) {
    if (!(e instanceof ApiError) || (e.status !== 404 && e.status !== 0 && e.status < 500)) throw e
  }
  let hits = await api.lookahead(wellId, md, LOOKAHEAD_M, 5)
  if (source !== 'all' && hits.length) {
    const keep = new Set((await api.events({ ids: hits.map((h) => h.event_id), source })).map((e) => e.event_id))
    hits = hits.filter((h) => keep.has(h.event_id))
  }
  return precedentFallback(hits, md)
}
