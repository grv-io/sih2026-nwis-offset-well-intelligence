// In-browser replay for the static demo. Plays a timeline recorded from the real
// server (scripts/build_static_demo.py, section "rig"): the well's drilling-log
// samples plus every change to the open-alert set, stamped with the sample time
// it happened at. Time advances like nwis/live/replay.py (sim time = start sample
// time + wall time x speed), and each alert shows "Recommendation pending..." for
// a few seconds before the recorded, cited recommendation fills in, the way the
// live system's background recommendation thread does.
import { ApiError } from '../errors'
import type { Alert, LiveSample, LiveState, Severity } from '../types'

export interface RigSamples {
  well_id: string
  n: number
  cols: Record<string, (number | null)[]>
  formations: [number, string | null][]
}

export interface RunEvent {
  t_s: number
  type: 'add' | 'update' | 'remove'
  alert?: Alert
  alert_id?: string
  severity?: Severity
  citations?: string[]
}

export interface RigRun {
  well_id: string
  start_md: number
  start_idx: number
  signals_unavailable: string[]
  events: RunEvent[]
  recommendations: Record<string, { recommendation: string | null; citations: string[] }>
}

const WINDOW_SIZE = 120 // nwis/live/replay.py WINDOW_SIZE
export const RECOMMENDATION_DELAY_MS = 6000
const SPARK_COLS = ['t_s', 'depth_md_m', 'torque_kftlb', 'pit_vol_bbl', 'gas_pct', 'rop_m_hr',
  'spp_psi', 'flow_in_gpm', 'flow_out_gpm', 'wob_klbf', 'mw_ppg'] as const

interface OpenAlert {
  recordedId: string
  base: Alert
  severity: Severity
  citations: string[]
  acknowledged: boolean
  shownAt: number
}

let alertSerial = 0 // alert ids keep counting across sessions, like the server's global counter

export class StaticReplaySession {
  readonly wellId: string
  readonly speed: number
  private readonly s: RigSamples
  private readonly run: RigRun
  private readonly t: (number | null)[]
  private readonly md: (number | null)[]
  private readonly mdRange: [number, number] | null
  private idx: number // next sample to consume
  private lastIdx: number | null // sample shown as "last" (consumed, or the seek target)
  private windowStart: number
  private simT0: number
  private wallT0: number
  private paused = false
  private running = false
  private evPtr = 0
  private formation: string | null = null
  private open = new Map<string, OpenAlert>() // keyed by display id
  private idMap = new Map<string, string>() // recorded id -> display id

  constructor(samples: RigSamples, run: RigRun, speed: number, now = Date.now()) {
    this.wellId = run.well_id
    this.speed = speed
    this.s = samples
    this.run = run
    this.t = samples.cols.t_s
    this.md = samples.cols.depth_md_m
    const mds = this.md.filter((v): v is number => typeof v === 'number')
    this.mdRange = mds.length ? [mds.reduce((a, b) => Math.min(a, b)), mds.reduce((a, b) => Math.max(a, b))] : null
    this.idx = run.start_md > 0 ? run.start_idx : 0
    this.lastIdx = run.start_md > 0 ? this.idx : null
    this.windowStart = Math.max(0, this.idx - WINDOW_SIZE + 1)
    this.simT0 = this.t[this.idx] ?? 0
    this.wallT0 = now
    this.evPtr = this.firstEventAtOrAfter(this.simT0)
    this.running = true
  }

  private firstEventAtOrAfter(t: number) {
    const ev = this.run.events
    let i = 0
    while (i < ev.length && ev[i].t_s < t) i++
    return i
  }

  private formationAt(i: number): string | null {
    let f: string | null = null
    for (const [start, name] of this.s.formations) {
      if (start > i) break
      f = name
    }
    return f
  }

  /** Consume every sample whose time has come (called on each poll). */
  advance(now = Date.now()) {
    if (!this.running || this.paused) return
    const target = this.simT0 + ((now - this.wallT0) / 1000) * this.speed
    while (this.idx < this.s.n && (this.t[this.idx] ?? Infinity) <= target) {
      const i = this.idx
      this.lastIdx = i
      this.formation = this.formationAt(i) ?? this.formation
      this.idx++
      const ts = this.t[i] ?? 0
      const ev = this.run.events
      while (this.evPtr < ev.length && ev[this.evPtr].t_s <= ts) this.apply(ev[this.evPtr++], now)
    }
  }

  private apply(e: RunEvent, now: number) {
    if (e.type === 'add' && e.alert) {
      if (this.idMap.has(e.alert.alert_id)) return
      const display = `ALERT-${String(++alertSerial).padStart(6, '0')}`
      this.idMap.set(e.alert.alert_id, display)
      this.open.set(display, {
        recordedId: e.alert.alert_id,
        base: { ...e.alert, alert_id: display },
        severity: e.alert.severity,
        citations: [...e.alert.citations],
        acknowledged: false,
        shownAt: now,
      })
      return
    }
    const display = e.alert_id ? this.idMap.get(e.alert_id) : undefined
    const a = display ? this.open.get(display) : undefined
    if (!a || !display) return
    if (e.type === 'update') {
      if (e.severity) a.severity = e.severity
      if (e.citations) a.citations = [...e.citations]
    } else if (e.type === 'remove') {
      this.open.delete(display)
    }
  }

  private materialise(a: OpenAlert, now: number): Alert {
    const rec = this.run.recommendations[a.recordedId]
    const ready = !!rec && now - a.shownAt >= RECOMMENDATION_DELAY_MS
    // same merge as nwis/live/alerts.py _recommend_blocking: recommendation citations first,
    // then anything escalation appended, minus the live-sensor placeholder
    const citations = ready
      ? [...rec.citations, ...a.citations.filter((c) => !rec.citations.includes(c) && !c.includes('live sensors'))]
      : a.citations
    return {
      ...a.base,
      severity: a.severity,
      recommendation: ready ? rec.recommendation : a.base.recommendation,
      citations,
      acknowledged: a.acknowledged,
    }
  }

  alerts(now = Date.now()): Alert[] {
    this.advance(now)
    return [...this.open.values()].map((a) => this.materialise(a, now)).sort((x, y) => y.t_s - x.t_s)
  }

  private sample(i: number): LiveSample {
    const out: Record<string, number | undefined> = {}
    for (const k of SPARK_COLS) {
      const v = this.s.cols[k]?.[i]
      if (v !== null && v !== undefined) out[k] = v
    }
    return out as unknown as LiveSample
  }

  state(window = 60, now = Date.now()): LiveState {
    this.advance(now)
    const n = this.s.n
    const last = this.lastIdx
    const end = this.idx - 1 // last consumed sample
    const from = Math.max(this.windowStart, end - window + 1)
    const win: LiveSample[] = []
    if (window > 0) for (let i = from; i <= end; i++) win.push(this.sample(i))
    return {
      well_id: this.wellId,
      speed: this.speed,
      running: this.running,
      paused: this.paused,
      finished: this.idx >= n,
      progress: n ? this.idx / n : 0,
      md_range: this.mdRange,
      t_s: last !== null ? this.t[last] : null,
      depth_md_m: last !== null ? this.md[last] : null,
      formation: this.formation,
      last_sample: last !== null ? this.sample(last) : null,
      window: win,
      n_alerts_open: [...this.open.values()].filter((a) => !a.acknowledged).length,
      signals_unavailable: [...this.run.signals_unavailable],
    }
  }

  pause(now = Date.now()) {
    this.advance(now)
    this.paused = true
  }

  resume(now = Date.now()) {
    if (!this.paused) return
    if (this.lastIdx !== null) this.simT0 = this.t[this.lastIdx] ?? this.simT0
    this.wallT0 = now
    this.paused = false
  }

  stop(now = Date.now()) {
    this.advance(now)
    this.running = false
  }

  /** nwis/live/replay.py seek(): jump to the sample nearest `md`; the alert engine keeps its state. */
  seek(md: number, now = Date.now()) {
    this.advance(now)
    let best = 0
    let bestD = Infinity
    this.md.forEach((v, i) => {
      if (typeof v === 'number' && Math.abs(v - md) < bestD) {
        bestD = Math.abs(v - md)
        best = i
      }
    })
    this.idx = best
    this.lastIdx = best
    this.windowStart = Math.max(0, best - WINDOW_SIZE + 1)
    this.simT0 = this.t[best] ?? 0
    this.wallT0 = now
    this.evPtr = this.firstEventAtOrAfter(this.simT0)
  }

  ack(displayId: string) {
    const a = this.open.get(displayId)
    if (!a) throw new ApiError(404, `alert '${displayId}' not found among open alerts`)
    a.acknowledged = true
  }
}
