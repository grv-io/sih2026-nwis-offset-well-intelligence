export const fmtNum = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : v.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })

export const fmtDepth = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${fmtNum(v)} m`)

export const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`)

const CARDINALS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
export const cardinal = (deg: number) => CARDINALS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]

export function fmtAgo(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}

/** Replay clock: seconds since spud, shown as d hh:mm. */
export function fmtRigClock(t: number | null | undefined): string {
  if (t === null || t === undefined) return '—'
  const d = Math.floor(t / 86400)
  const h = Math.floor((t % 86400) / 3600)
  const m = Math.floor((t % 3600) / 60)
  return `${d}d ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}
