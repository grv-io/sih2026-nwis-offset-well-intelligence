import { en } from '../i18n/en'
import type { HazardFamily, Severity } from './types'

// English event labels, derived from the i18n dictionary. UI code should use
// labelForEventType / useEventLabel (src/i18n) so Hindi works; this English form
// is kept for matching against the (English) report archive, e.g. highlighting.
export const EVENT_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(en)
    .filter(([k]) => k.startsWith('event.'))
    .map(([k, v]) => [k.slice('event.'.length), v]),
)

export const eventLabel = (t: string | null | undefined) => (t ? EVENT_LABEL[t] ?? t.replace(/_/g, ' ') : '—')

export const FAMILY_OF: Record<string, HazardFamily> = {
  mud_loss: 'losses',
  stuck_pipe: 'stuck', wellbore_instability: 'stuck', fishing_operation: 'stuck', twist_off: 'stuck', lost_bha: 'stuck',
  kick: 'well_control', overpressure: 'well_control', gas_show: 'well_control',
  cementing_issue: 'other', torque_spike: 'other', npt_other: 'other',
}

/** Categorical slots validated all-pairs on the dark basemap (styles/tokens.css
 *  --fam*). Labels: i18n keys `family.<key>` / `family.<key>.members`. */
export const FAMILY: Record<HazardFamily, { color: string }> = {
  losses: { color: '#3987e5' },
  stuck: { color: '#d95926' },
  well_control: { color: '#199e70' },
  other: { color: '#78828e' },
}

/** Colour comes from the CSS token (rgbVar), so it re-tunes per theme; the
 *  label is the English fallback (i18n key `sev.<severity>`). */
export const SEVERITY_META: Record<Severity, { label: string; priority: string; rgbVar: string; rank: number }> = {
  critical: { label: 'Critical', priority: 'P1', rgbVar: '--crit', rank: 3 },
  high: { label: 'High', priority: 'P2', rgbVar: '--high', rank: 2 },
  medium: { label: 'Medium', priority: 'P3', rgbVar: '--med', rank: 1 },
  low: { label: 'Low', priority: 'P4', rgbVar: '--low', rank: 0 },
}

export function normaliseSeverity(s: string | null | undefined): Severity {
  const v = String(s ?? '').toLowerCase()
  return (v === 'critical' || v === 'high' || v === 'medium' || v === 'low' ? v : 'medium') as Severity
}

/** Alert rule ids (nwis/live/alerts.py). Labels: i18n keys `rule.<id>` / `rule.<id>.hint`. */
export const RULES = ['precedent_zone', 'param_anomaly', 'model_risk'] as const

/** Similarity dimensions in display order (nwis/geo/nearby.py). Labels: i18n
 *  keys `dim.<key>` and `dim.<key>.short`. */
export const DIMENSIONS: { key: 'geographic' | 'formation_overlap' | 'trajectory_type' | 'target_depth' | 'difficulty' }[] = [
  { key: 'geographic' },
  { key: 'formation_overlap' },
  { key: 'trajectory_type' },
  { key: 'target_depth' },
  { key: 'difficulty' },
]
