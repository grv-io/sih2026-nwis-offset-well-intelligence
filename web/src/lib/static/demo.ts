// Static demo client (VITE_STATIC_DEMO=1, the GitHub Pages build). Same surface as
// the live `api` in ../api.ts, but every GET is answered from JSON recorded from
// the real API by scripts/build_static_demo.py (web/public/demo-data/), and the
// few POSTs are simulated in memory:
//   - review approve/reject change this tab's copy of the event ledger;
//   - /answer replays the recorded answer for a recorded question, runs the
//     scope guard (port of nwis/search/answer.py) for greetings/off-topic, and
//     otherwise says plainly that only the listed questions are recorded;
//   - the rig replay plays a recorded server timeline in the browser (./replay.ts).
// Only files listed in an index are fetched, so a request outside the recording
// never produces a 404; it raises StaticMissError instead.
import type { Api, FigureJson } from '../api'
import { ApiError, StaticMissError } from '../errors'
import type {
  Alert, Answer, Basin, DipFit, DocumentText, DrillingEvent, Health, LiveState, LookaheadHit,
  MapData, MapWell, OffsetCandidate, ReviewQueue, SearchHit, Source, Summary, Well,
} from '../types'
import { guardAnswer, queryKind, staticUnknownAnswer } from './guard'
import { StaticReplaySession, type RigRun, type RigSamples } from './replay'
import { resolveRef, type DocIndexEntry } from './resolve'

const DATA = import.meta.env.BASE_URL + 'demo-data/'
const TRUTH = 'synthetic_truth'
const RADII = Array.from({ length: 19 }, (_, i) => 1 + 0.5 * i)

const files = new Map<string, Promise<unknown>>()

function load<T>(path: string): Promise<T> {
  let p = files.get(path)
  if (!p) {
    p = fetch(DATA + path, { headers: { Accept: 'application/json' } }).then((r) => {
      if (!r.ok) throw new ApiError(r.status, `static demo data not found: ${path}`)
      return r.json()
    })
    p.catch(() => files.delete(path))
    files.set(path, p)
  }
  return p as Promise<T>
}

const copy = <T>(v: T): T => (typeof structuredClone === 'function' ? structuredClone(v) : JSON.parse(JSON.stringify(v)))

/** Must match norm_question() in scripts/build_static_demo.py. */
export function normQuestion(s: string): string {
  return s.trim().toLowerCase().replace(/\s+/g, ' ').replace(/[\s?.!।]+$/u, '')
}

/** Must match corr_key() in scripts/build_static_demo.py: active well first, rest sorted. */
export function corrKey(ids: string[]): string {
  return [ids[0], ...ids.slice(1).sort()].join('_')
}

function snapRadius(r: number): number {
  return RADII.reduce((best, x) => (Math.abs(x - r) < Math.abs(best - r) ? x : best), RADII[0])
}

// --------------------------------------------------------------------------- //
// event ledger + review queue (mutable for this tab)
// --------------------------------------------------------------------------- //
const ledgers = new Map<Basin, Promise<DrillingEvent[]>>()
function ledger(basin: Basin): Promise<DrillingEvent[]> {
  let p = ledgers.get(basin)
  if (!p) {
    p = load<DrillingEvent[]>(`events/${basin}.json`).then((rows) => copy(rows))
    p.catch(() => ledgers.delete(basin))
    ledgers.set(basin, p)
  }
  return p
}

const sourceOk = (method: string, source: Source) =>
  source === 'all' ? true : source === 'truth' ? method === TRUTH : method !== TRUTH

const byWellDepth = (a: DrillingEvent, b: DrillingEvent) =>
  a.well_id.localeCompare(b.well_id) || (a.depth_md_m ?? 1e9) - (b.depth_md_m ?? 1e9)

async function reviewQueue(): Promise<ReviewQueue> {
  const [base, rows] = await Promise.all([load<ReviewQueue>('review-queue.json'), ledger('assam')])
  const byId = new Map(rows.map((e) => [e.event_id, e]))
  const extracted = rows.filter((e) => e.extraction_method !== TRUTH)
  const items = base.items
    .filter((i) => byId.has(i.event_id) && !byId.get(i.event_id)!.reviewed_by_human)
    .map((i) => ({ ...copy(byId.get(i.event_id)!), reason: i.reason }))
  return {
    threshold: base.threshold,
    counters: {
      extracted: extracted.length,
      reviewed: extracted.filter((e) => e.reviewed_by_human).length,
      pending: items.length,
    },
    items,
  }
}

// --------------------------------------------------------------------------- //
// documents
// --------------------------------------------------------------------------- //
interface DocFile extends Omit<DocumentText, 'page' | 'text' | 'text_source'> {
  pages: Record<string, { text: string; text_source: string }>
}

async function resolveDocument(ref: string) {
  const docs = await load<DocIndexEntry[]>('docs/index.json')
  const hit = resolveRef(docs, ref)
  if (!hit) throw new ApiError(404, `no document matches '${ref}'`)
  return hit
}

async function documentText(documentId: string, page = 1): Promise<DocumentText> {
  const { document_id } = await resolveDocument(documentId)
  const d = await load<DocFile>(`docs/${encodeURIComponent(document_id)}.json`)
  const nums = Object.keys(d.pages).map(Number)
  const p = d.pages[String(page)] ?? d.pages[String(Math.min(page, Math.max(...nums)))] ?? { text: '', text_source: 'unavailable' }
  const { pages: _pages, ...meta } = d
  return { ...meta, page, text: p.text, text_source: p.text_source }
}

// --------------------------------------------------------------------------- //
// memory: recorded questions + keyword search over the chunk table
// --------------------------------------------------------------------------- //
interface QaIndexEntry { question: string; norm: string; file: string }
interface QaFile { question: string; search: SearchHit[]; answer: Answer }
interface Chunk { chunk_id: string; well_id: string; page_ref: string; document_id: string; text: string }

async function recorded(q: string): Promise<QaFile | null> {
  const idx = await load<QaIndexEntry[]>('qa/index.json')
  const e = idx.find((x) => x.norm === normQuestion(q))
  return e ? load<QaFile>(e.file) : null
}

/** Recorded questions, in the order the Memory panel offers them. */
export async function recordedQuestions(): Promise<string[]> {
  return (await load<QaIndexEntry[]>('qa/index.json')).map((x) => x.question)
}

async function keywordSearch(q: string, k = 8, wellIds?: string[]): Promise<SearchHit[]> {
  // Same token rule as nwis/search/retrieve.py sanitize_fts_query (alnum, len >= 2, OR-ed).
  const terms = [...new Set((q.match(/[A-Za-z0-9]+/g) ?? []).filter((t) => t.length >= 2).map((t) => t.toLowerCase()))]
  if (!terms.length) return []
  const chunks = await load<Chunk[]>('chunks.json')
  const allowed = wellIds?.length ? new Set(wellIds) : null
  const res = terms.map((t) => new RegExp(`(?<![A-Za-z0-9])${t}(?![A-Za-z0-9])`, 'gi'))
  const scored: { c: Chunk; s: number }[] = []
  for (const c of chunks) {
    if (allowed && !allowed.has(c.well_id)) continue
    let s = 0
    for (const re of res) {
      const n = c.text.match(re)?.length ?? 0
      if (n) s += 1 + Math.log(n)
    }
    if (s > 0) scored.push({ c, s })
  }
  scored.sort((a, b) => b.s - a.s || a.c.chunk_id.localeCompare(b.c.chunk_id))
  return scored.slice(0, k).map(({ c }, rank) => ({
    chunk_id: c.chunk_id, well_id: c.well_id, page_ref: c.page_ref, document_id: c.document_id, text: c.text,
    score: 1 / (60 + rank + 1), why: `keyword #${rank + 1} (static demo)`,
  }))
}

// --------------------------------------------------------------------------- //
// rig replay
// --------------------------------------------------------------------------- //
interface RigIndex { speed_recorded: number; wells: Record<string, { starts: number[] }> }

export async function rigIndex(): Promise<RigIndex> {
  return load<RigIndex>('rig/index.json')
}

let session: StaticReplaySession | null = null

function requireSession(): StaticReplaySession {
  if (!session) throw new ApiError(409, 'no active replay session; call POST /live/start first')
  return session
}

async function startAt(wellId: string, speed: number, startMd: number | null) {
  const idx = await rigIndex()
  const w = idx.wells[wellId]
  if (!w) throw new StaticMissError(`well '${wellId}' has no recorded replay in this static demo`)
  const want = startMd && startMd > 0 ? startMd : 0
  const start = w.starts.reduce((best, s) => (Math.abs(s - want) < Math.abs(best - want) ? s : best), w.starts[0])
  const [samples, run] = await Promise.all([
    load<RigSamples>(`rig/${wellId}/samples.json`),
    load<RigRun>(`rig/${wellId}/run_${start}.json`),
  ])
  session?.stop()
  session = new StaticReplaySession(samples, run, speed)
  return { status: 'started', well_id: wellId, speed, start_md: start || null, requested_start_md: startMd }
}

// --------------------------------------------------------------------------- //
export const staticApi: Api = {
  health: () => load<Health>('health.json'),
  summary: () => load<Summary>('summary.json'),
  wells: () => load<Well[]>('wells.json'),

  mapData: async (well_id: string | null, radius_km: number, source: Source, basin: Basin = 'assam'): Promise<MapData> => {
    const list = await load<{ wells: MapWell[]; note?: string | null }>(`map/${basin}/wells_${source}.json`)
    let candidates: OffsetCandidate[] = []
    let ring: [number, number][] = []
    if (well_id) {
      if (!list.wells.some((w) => w.well_id === well_id)) throw new ApiError(404, `well '${well_id}' not found`)
      const r = snapRadius(radius_km)
      const d = await load<{ candidates: OffsetCandidate[]; ring: [number, number][] }>(`map/${basin}/${well_id}_r${r}.json`)
      candidates = d.candidates
      ring = d.ring
    }
    const out: MapData = { active_well_id: well_id, radius_km, source, wells: list.wells, candidates, ring }
    if (basin === 'volve' && list.note) out.note = list.note
    return copy(out)
  },

  events: async (o) => {
    const rows = await ledger(o.basin ?? 'assam')
    const source = o.source ?? 'extracted'
    let out: DrillingEvent[]
    if (o.ids?.length) {
      const wanted = new Set(o.ids)
      out = rows.filter((e) => wanted.has(e.event_id) && sourceOk(e.extraction_method, source))
    } else {
      const wells = o.well_ids?.length ? new Set(o.well_ids) : null
      out = rows.filter(
        (e) =>
          (!wells || wells.has(e.well_id)) &&
          sourceOk(e.extraction_method, source) &&
          (!o.event_type || e.event_type === o.event_type) &&
          (!o.formation || e.formation === o.formation),
      )
    }
    return copy(out.sort(byWellDepth))
  },

  correlationFigure: async (well_ids: string[], mode: 'tvd' | 'normalised', source: Source) => {
    if (!well_ids.length) throw new ApiError(400, 'well_ids must be a non-empty comma-separated list')
    const key = corrKey(well_ids)
    const keys = await load<string[]>('corr/index.json')
    if (!keys.includes(key)) throw new StaticMissError(`correlation panel ${well_ids.join(', ')} is not recorded in this static demo`)
    return copy(await load<{ figure: FigureJson; n_events: number }>(`corr/${mode}_${source}_${key}.json`))
  },

  dip: (formation: string) => load<DipFit>(`dip/${encodeURIComponent(formation)}.json`),

  lookahead: async (): Promise<LookaheadHit[]> => {
    throw new StaticMissError('lookahead is served through the recorded risk grid in this static demo')
  },

  risk: async (well_id: string, md: number) => {
    const wells = await load<Well[]>('wells.json')
    if (!wells.some((w) => w.well_id === well_id)) throw new ApiError(404, `well '${well_id}' not found`)
    const f = await load<{ step_m: number; by_md: Record<string, unknown[]> }>(`risk/${well_id}.json`)
    const depths = Object.keys(f.by_md).map(Number)
    const nearest = depths.reduce((best, d) => (Math.abs(d - md) < Math.abs(best - md) ? d : best), depths[0])
    return { intervals: copy(f.by_md[String(nearest)]), static_md: nearest }
  },

  search: async (q: string, k = 8) => {
    const rec = await recorded(q)
    if (!rec) throw new StaticMissError('hybrid (embedding) search is recorded for the example questions only')
    return copy(rec.search.slice(0, k))
  },

  searchKeyword: (q: string, k = 8, well_ids?: string[]) => keywordSearch(q, k, well_ids),

  answer: async (query: string, _use_cache: boolean, lang: 'en' | 'hi' = 'en'): Promise<Answer> => {
    const kind = queryKind(query)
    if (kind) return guardAnswer(kind, lang)
    const rec = await recorded(query)
    if (rec) return copy(rec.answer)
    return staticUnknownAnswer(lang, await recordedQuestions())
  },

  resolveDocument,
  documentText,
  reviewQueue,

  approve: async (id: string) => {
    const rows = await ledger('assam')
    const e = rows.find((x) => x.event_id === id)
    if (!e) throw new ApiError(404, `event '${id}' not found`)
    e.reviewed_by_human = true
    return { status: 'approved' }
  },

  reject: async (id: string) => {
    const rows = await ledger('assam')
    const i = rows.findIndex((x) => x.event_id === id)
    if (i < 0) throw new ApiError(404, `event '${id}' not found`)
    if (rows[i].extraction_method === TRUTH) throw new ApiError(409, 'ground-truth events cannot be rejected')
    rows.splice(i, 1)
    return { status: 'rejected' }
  },

  live: {
    state: async (window = 60): Promise<LiveState> => requireSession().state(window),
    alerts: async (): Promise<Alert[]> => requireSession().alerts(),
    start: async (well_id: string, speed: number) => {
      await startAt(well_id, speed, null)
      return requireSession().state(60)
    },
    startAt: (well_id: string, speed: number, start_md: number | null) => startAt(well_id, speed, start_md),
    pause: async () => (requireSession().pause(), { status: 'paused' }),
    resume: async () => (requireSession().resume(), { status: 'resumed' }),
    stop: async () => (requireSession().stop(), { status: 'stopped' }),
    seek: async (md: number) => {
      const s = requireSession()
      s.seek(md)
      return s.state(60)
    },
    ack: async (id: string) => (requireSession().ack(id), { status: 'acked', alert_id: id }),
  },
}
