import { normaliseSeverity, SEVERITY_META } from '../lib/hazards'
import { useT } from '../i18n'
import type { Severity } from '../lib/types'

// ISA-101: priority is carried by colour AND shape AND text, so it survives
// colour-blindness, a washed-out projector and a greyscale printout.
function Shape({ severity, size }: { severity: Severity; size: number }) {
  const c = `rgb(var(${SEVERITY_META[severity].rgbVar}))`
  const s = size
  switch (severity) {
    case 'critical': // octagon
      return (
        <svg width={s} height={s} viewBox="0 0 12 12" aria-hidden>
          <path d="M3.5 0.5h5l3 3v5l-3 3h-5l-3-3v-5z" fill={c} />
        </svg>
      )
    case 'high': // triangle
      return (
        <svg width={s} height={s} viewBox="0 0 12 12" aria-hidden>
          <path d="M6 0.8l5.6 10.4H0.4z" fill={c} />
        </svg>
      )
    case 'medium': // diamond
      return (
        <svg width={s} height={s} viewBox="0 0 12 12" aria-hidden>
          <path d="M6 0.5l5.5 5.5L6 11.5 0.5 6z" fill={c} />
        </svg>
      )
    default: // circle
      return (
        <svg width={s} height={s} viewBox="0 0 12 12" aria-hidden>
          <circle cx="6" cy="6" r="5" fill={c} />
        </svg>
      )
  }
}

export interface SeverityBadgeProps {
  severity: string
  size?: 'sm' | 'lg'
  showPriority?: boolean
  muted?: boolean
}

export function SeverityBadge({ severity, size = 'sm', showPriority = false, muted = false }: SeverityBadgeProps) {
  const { t } = useT()
  const sev = normaliseSeverity(severity)
  const meta = SEVERITY_META[sev]
  const label = t(`sev.${sev}`)
  const lg = size === 'lg'
  return (
    <span
      data-testid="severity-badge"
      data-severity={sev}
      role="img"
      aria-label={t(muted ? 'sev.ariaAcked' : 'sev.aria', { label })}
      className={
        'inline-flex items-center gap-1.5 rounded-sm border font-semibold uppercase tracking-[0.06em] whitespace-nowrap ' +
        (lg ? 'h-7 px-2.5 text-xs' : 'h-5 px-1.5 text-micro') +
        (muted ? ' opacity-60' : '')
      }
      style={{
        color: `rgb(var(${meta.rgbVar}))`,
        borderColor: `rgb(var(${meta.rgbVar}) / 0.5)`,
        background: `rgb(var(${meta.rgbVar}) / 0.12)`,
      }}
    >
      <Shape severity={sev} size={lg ? 12 : 10} />
      {showPriority && <span className="num">{meta.priority}</span>}
      <span>{label}</span>
    </span>
  )
}
