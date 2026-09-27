// Thin fetch layer. Everything goes through /api (see vite.config.ts).
import type {
  Alert, Answer, Basin, DipFit, DocumentText, DrillingEvent, Health, LiveState, LookaheadHit,
  MapData, ReviewQueue, SearchHit, Source, Summary, Well,
} from './types'

export const API_BASE = '/api'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

type Init = RequestInit & { timeoutMs?: number }

async function request<T>(path: string, init: Init = {}): Promise<T> {
  const ctrl = new AbortController()
  const timer = init.timeoutMs ? setTimeout(() => ctrl.abort(), init.timeoutMs) : undefined
  try {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (init.body) headers['Content-Type'] = 'application/json'
    const res = await fetch(API_BASE + path, { ...init, signal: init.signal ?? ctrl.signal, headers })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const j = await res.json()
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail)
    }
    return (await res.json()) as T
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw new ApiError(0, 'request timed out')
    throw e
  } finally {
    if (timer) clearTimeout(timer)
  }
}

export function qs(o: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? '?' + s : ''
}

export interface FigureJson {
  data: Record<string, unknown>[]
  layout: Record<string, unknown>
}

export const api = {
  health: () => request<Health>('/health', { timeoutMs: 8000 }),
  summary: () => request<Summary>('/ui/summary'),
  wells: () => request<Well[]>('/wells'),
  mapData: (well_id: string | null, radius_km: number, source: Source, basin: Basin = 'assam') =>
    request<MapData>('/map/data' + qs({ well_id, radius_km, source, basin })),
  events: (o: { well_ids?: string[]; source?: Source; ids?: string[]; event_type?: string; formation?: string; basin?: Basin }) =>
    request<DrillingEvent[]>('/events' + qs({
      well_ids: o.well_ids?.join(','), ids: o.ids?.join(','), source: o.source,
      event_type: o.event_type, formation: o.formation, basin: o.basin,
    })),
  correlationFigure: (well_ids: string[], mode: 'tvd' | 'normalised', source: Source) =>
    request<{ figure: FigureJson; n_events: number }>(
      '/correlation/figure' + qs({ well_ids: well_ids.join(','), mode, source })),
  dip: (formation: string) => request<DipFit>('/dip/' + encodeURIComponent(formation)),
  lookahead: (well_id: string, md: number, lookahead_m = 200, radius_km = 5) =>
    request<LookaheadHit[]>('/lookahead' + qs({ well_id, md, lookahead_m, radius_km }), { timeoutMs: 30000 }),
  risk: (well_id: string, md: number) => request<unknown>('/risk' + qs({ well_id, md }), { timeoutMs: 30000 }),
  search: (q: string, k = 8, well_ids?: string[]) =>
    request<SearchHit[]>('/search' + qs({ q, k, well_ids: well_ids?.join(',') }), { timeoutMs: 15000 }),
  searchKeyword: (q: string, k = 8, well_ids?: string[]) =>
    request<SearchHit[]>('/search/keyword' + qs({ q, k, well_ids: well_ids?.join(',') })),
  // lang: which language leads the chatbot's fixed greeting/off-topic line (api/search_router.py)
  answer: (query: string, use_cache: boolean, lang: 'en' | 'hi' = 'en') =>
    request<Answer>('/answer', { method: 'POST', body: JSON.stringify({ query, use_cache, lang }), timeoutMs: 180000 }),
  resolveDocument: (ref: string) =>
    request<{ document_id: string; well_id: string; page: number }>('/documents/resolve' + qs({ ref })),
  documentText: (document_id: string, page = 1) =>
    request<DocumentText>('/documents/' + encodeURIComponent(document_id) + '/text' + qs({ page })),
  reviewQueue: () => request<ReviewQueue>('/review-queue'),
  approve: (id: string) => request<{ status: string }>('/review-queue/' + encodeURIComponent(id) + '/approve', { method: 'POST' }),
  reject: (id: string) => request<{ status: string }>('/review-queue/' + encodeURIComponent(id) + '/reject', { method: 'POST' }),

  live: {
    state: (window = 60) => request<LiveState>('/ui/live-state' + qs({ window }), { timeoutMs: 4000 }),
    alerts: () => request<Alert[]>('/live/alerts?include_acked=true', { timeoutMs: 4000 }),
    start: (well_id: string, speed: number) =>
      request<LiveState>('/live/start', { method: 'POST', body: JSON.stringify({ well_id, speed }), timeoutMs: 20000 }),
    startAt: (well_id: string, speed: number, start_md: number | null) =>
      request<unknown>('/ui/live/start', { method: 'POST', body: JSON.stringify({ well_id, speed, start_md }), timeoutMs: 60000 }),
    pause: () => request<unknown>('/live/pause', { method: 'POST' }),
    resume: () => request<unknown>('/live/resume', { method: 'POST' }),
    stop: () => request<unknown>('/live/stop', { method: 'POST' }),
    seek: (md: number) => request<LiveState>('/live/seek', { method: 'POST', body: JSON.stringify({ md }), timeoutMs: 20000 }),
    ack: (id: string) => request<unknown>('/live/alerts/' + encodeURIComponent(id) + '/ack', { method: 'POST' }),
  },
}
