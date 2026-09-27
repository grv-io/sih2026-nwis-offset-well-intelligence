import { useState } from 'react'
import { useT } from '../../i18n'

const UNIT: [RegExp, string][] = [
  [/_zscore$/, ' z'],
  [/_gpm$/, ' gpm'],
  [/_bbl$/, ' bbl'],
  [/_psi$/, ' psi'],
  [/_pct$/, ' %'],
]

function prettyKey(k: string) {
  let unit = ''
  for (const [re, u] of UNIT) {
    if (re.test(k)) {
      unit = u
      k = k.replace(re, '')
      break
    }
  }
  const name = k.replace(/_/g, ' ').replace(/\b(spp|rop|wob|mw|ecd)\b/gi, (m) => m.toUpperCase())
  return { name, unit }
}

/** "pit_loss anomaly: {'pit_vol_zscore': -1.01, 'flow_in_gpm': 634.7}" ->
 *  "Pit loss anomaly — pit vol z −1.01 · flow in 634.7 gpm" (nwis/live/alerts.py
 *  formats the evidence dict with Python repr). Other messages pass through. */
export function prettyAlertMessage(msg: string): string {
  const m = msg.match(/^(\w+) anomaly:\s*\{(.*)\}\s*$/)
  if (!m) return msg
  const parts = [...m[2].matchAll(/'([^']+)':\s*(-?[\d.eE+-]+)/g)].map(([, k, v]) => {
    const { name, unit } = prettyKey(k)
    const n = Number(v)
    const val = Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2)
    return unit === ' z' ? `${name} z-score ${val}` : `${name} ${val}${unit}`
  })
  const rule = m[1].replace(/_/g, ' ')
  return `${rule.charAt(0).toUpperCase()}${rule.slice(1)} pattern: ${parts.join(' · ')}`
}

/** LLM recommendations come back as light markdown; render headings/bullets/bold
 *  without a markdown dependency, clamped with an expander (rig screens are small). */
export function RecommendationText({ text }: { text: string }) {
  const { t } = useT()
  const [open, setOpen] = useState(false)
  const lines = text
    .replace(/\[[^\]|]+\|[^\]]+\]/g, '') // inline citation tags are shown as chips below
    .split('\n')
    .map((l) => l.trimEnd())
    .filter((l, i, arr) => l.trim() || (i > 0 && arr[i - 1].trim()))
  const long = lines.length > 5 || text.length > 420
  const render = (l: string, i: number) => {
    const bold = (s: string) =>
      s.split(/\*\*(.+?)\*\*/g).map((p, j) => (j % 2 ? <b key={j} className="text-ink font-semibold">{p}</b> : <span key={j}>{p}</span>))
    if (/^#{1,4}\s/.test(l)) return <div key={i} className="text-ink font-semibold mt-1.5 first:mt-0">{l.replace(/^#+\s*/, '')}</div>
    if (/^\s*[-*]\s/.test(l)) return <div key={i} className="pl-3 -indent-3">· {bold(l.replace(/^\s*[-*]\s*/, ''))}</div>
    if (!l.trim()) return <div key={i} className="h-1.5" />
    return <div key={i}>{bold(l)}</div>
  }
  return (
    <div>
      <div lang="en" className={'text-base text-ink2 leading-6 ' + (long && !open ? 'max-h-[7.5rem] overflow-hidden [mask-image:linear-gradient(to_bottom,black_70%,transparent)]' : '')}>
        {lines.map(render)}
      </div>
      {long && (
        <button className="mt-1 text-xs text-accent hover:underline" onClick={() => setOpen((o) => !o)}>
          {open ? t('alert.showLess') : t('alert.showMore')}
        </button>
      )}
    </div>
  )
}
