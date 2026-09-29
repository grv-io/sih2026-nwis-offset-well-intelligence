import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Gauge, Info } from 'lucide-react'
import { fmtNum } from '../../lib/format'
import type { RiskInterval, Source, Well } from '../../lib/types'
import { CitationChip } from '../../components/CitationChip'
import { openCitation, useEvidence } from '../../components/Evidence'
import { Empty, ErrorNote, Loading } from '../../components/ui'
import { loadRisk } from './risk'
import { en } from '../../i18n/en'
import { useEventLabel, useT, type MsgKey, type TFn } from '../../i18n'

// Components are shades of grey on purpose: a risk score is not an alarm, and the
// categorical hues on this page already mean "hazard family" on the map. The
// ramp is a token (--risk1..3) so the heaviest weight stays most salient in
// both themes (light-on-dark vs dark-on-light).
const COMPONENTS: readonly { key: 'precedent_component' | 'anomaly_component' | 'model_component'; label: MsgKey; color: string; w: number }[] = [
  { key: 'precedent_component', label: 'risk.cPrecedent', color: 'rgb(var(--risk1))', w: 0.5 },
  { key: 'anomaly_component', label: 'risk.cAnomaly', color: 'rgb(var(--risk2))', w: 0.3 },
  { key: 'model_component', label: 'risk.cModel', color: 'rgb(var(--risk3))', w: 0.2 },
]

type Mode = 'supervised' | 'indicator' | 'mixed'
/** Mode badge text. In Hindi the first (header) occurrence carries the English
 *  in brackets, "संकेतक (indicator)", so the term the judges hear still reads. */
function modeLabel(t: TFn, lang: string, mode: Mode, first = false) {
  const key = `mode.${mode}` as const
  return first && lang === 'hi' ? `${t(key)} (${en[key]})` : t(key)
}

function StackedBar({ r }: { r: RiskInterval }) {
  const { t } = useT()
  const parts = COMPONENTS.map((c) => ({ ...c, v: Math.max(0, (r[c.key] as number) * c.w) }))
  const sum = parts.reduce((s, p) => s + p.v, 0)
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-3.5 flex-1 rounded-sm bg-s3/60 overflow-hidden" role="img" aria-label={t('risk.scoreAria', { n: Math.round(r.score * 100) })}>
        <div className="absolute inset-y-0 left-0 flex gap-[2px]" style={{ width: `${r.score * 100}%` }}>
          {parts.map((p) =>
            p.v > 0 && sum > 0 ? (
              <span
                key={p.key}
                className="h-full first:rounded-l-sm last:rounded-r-sm"
                style={{ width: `${(p.v / sum) * 100}%`, background: p.color }}
                title={t('risk.componentTitle', { label: t(p.label), v: (r[p.key] as number).toFixed(2), w: p.w })}
              />
            ) : null,
          )}
        </div>
      </div>
      <span className="num w-10 text-right text-sm font-semibold text-ink" title={t('risk.scoreTitle')}>{r.score * 100 < 10 ? (r.score * 100).toFixed(1) : Math.round(r.score * 100)}</span>
    </div>
  )
}

export function RiskPanel({ well, source, bitMd, onBitMd }: {
  well: Well | undefined
  source: Source
  bitMd: number | null // from the URL (&md=), so a demo link lands on a chosen depth
  onBitMd: (md: number) => void
}) {
  const { t, lang } = useT()
  const eventLabel = useEventLabel()
  const open = useEvidence()
  const td = well?.td_md_m ?? 3000
  const initial = bitMd ?? Math.min(2400, Math.round(td * 0.7))
  const [md, setMd] = useState(initial)
  const committed = initial
  const setCommitted = (v: number) => onBitMd(v)
  useEffect(() => setMd(initial), [initial])

  const q = useQuery({
    queryKey: ['risk', well?.well_id, committed, source],
    queryFn: () => loadRisk(well!.well_id, committed, source),
    enabled: !!well,
    placeholderData: (p) => p,
    retry: 0,
  })
  const v = q.data
  const byInterval = new Map<number, RiskInterval[]>()
  for (const r of v?.intervals ?? []) byInterval.set(r.top_md_m, [...(byInterval.get(r.top_md_m) ?? []), r])

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex flex-wrap items-center gap-4 px-4 py-3 border-b border-rule">
        <label className="flex items-center gap-3 flex-1 min-w-[280px]">
          <span className="label whitespace-nowrap">{t('risk.bitDepth')}</span>
          <input
            type="range"
            min={0}
            max={Math.round(td)}
            step={10}
            value={md}
            onChange={(e) => setMd(Number(e.target.value))}
            onPointerUp={() => setCommitted(md)}
            onKeyUp={() => setCommitted(md)}
            className="flex-1 accent-[rgb(var(--accent))]"
            aria-label={t('risk.bitDepthAria')}
          />
          <span className="num text-lg font-semibold text-ink w-[84px] text-right">{fmtNum(md)} m</span>
        </label>
        {v && (
          <span
            className={'chip font-semibold uppercase tracking-[0.06em] ' + (v.mode === 'supervised' ? 'text-ink border-ink2/50' : 'text-dim')}
            title={v.mode === 'supervised'
              ? t('mode.supervisedTitle')
              : v.mode === 'mixed'
                ? t('mode.mixedTitle')
                : t('mode.indicatorTitle')}
            data-testid="risk-mode"
            data-mode={v.mode}
          >
            {modeLabel(t, lang, v.mode, true)}
          </span>
        )}
      </div>

      {v?.note && (
        <div className="mx-4 mt-3 flex items-start gap-2 text-xs text-dim">
          <Info size={14} className="mt-0.5 shrink-0" />
          <span>{v.origin === 'precedent-fallback' ? t('risk.fallbackNote') : v.note}</span>
        </div>
      )}

      {v?.staticMd !== undefined && v.staticMd !== committed && (
        <div className="mx-4 mt-3 flex items-start gap-2 text-xs text-dim" data-testid="risk-static-note">
          <Info size={14} className="mt-0.5 shrink-0" />
          <span>{t('static.riskSnapped', { md: fmtNum(v.staticMd) })}</span>
        </div>
      )}

      <div className="flex items-center gap-4 px-4 pt-3 text-xs text-dim">
        <span className="label">{t('risk.next', { n: fmtNum(200) })}</span>
        <span className="ml-auto flex items-center gap-3">
          {COMPONENTS.map((c) => (
            <span key={c.key} className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: c.color }} />
              {t(c.label)} <span className="num text-faint">×{c.w}</span>
            </span>
          ))}
        </span>
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-4 py-3">
        {q.isLoading ? (
          <Loading label={t('risk.scoring')} />
        ) : q.isError ? (
          <ErrorNote error={q.error} what={t('risk.what')} />
        ) : !v || v.intervals.length === 0 ? (
          <Empty icon={<Gauge size={28} />} title={t('risk.emptyTitle', { n: fmtNum(200), md: fmtNum(committed) })}>
            {t('risk.emptyBody', { src: source === 'truth' ? t('corr.srcTruth') : t('corr.srcExtracted') })}
          </Empty>
        ) : (
          <ol className="flex flex-col gap-3" data-testid="risk-intervals">
            {[...byInterval.entries()].map(([top, rows]) => (
              <li key={top} className="rounded border border-line bg-s1">
                <div className="flex items-center gap-3 px-3 h-8 border-b border-rule">
                  <span className="num text-sm font-semibold text-ink">{fmtNum(top)}–{fmtNum(top + 50)} m</span>
                  <span className="text-xs text-dim">{rows[0].formation ?? t('ui.formationNa')}</span>
                  <span className="ml-auto num text-xs text-faint">{top <= committed ? t('risk.bitHere') : t('risk.ahead', { n: fmtNum(top - committed) })}</span>
                </div>
                <div className="px-3 py-2 flex flex-col gap-2">
                  {rows.sort((a, b) => b.score - a.score).map((r) => (
                    <div key={r.hazard} className="grid grid-cols-[140px_1fr] items-start gap-3">
                      <div className="pt-[1px]">
                        <div className="text-sm text-ink2">{eventLabel(r.hazard)}</div>
                        {r.mode && <div className="text-micro uppercase tracking-[0.06em] text-faint" title={r.mode === 'supervised' ? t('mode.rowSupervisedTitle') : t('mode.rowIndicatorTitle')}>{modeLabel(t, lang, r.mode)}</div>}
                      </div>
                      <div className="flex flex-col gap-1.5">
                        <StackedBar r={r} />
                        <div className="flex flex-wrap items-center gap-1.5">
                          {[...new Set(r.top_reasons)].slice(0, 3).map((reason, i) =>
                            /#p\d+/.test(reason) ? (
                              <CitationChip key={i} citation={reason} onOpen={(c) => openCitation(open, c, t('risk.precedentSource'))} />
                            ) : (
                              <span key={i} className="chip text-dim" title={reason}>{prettyReason(reason, t)}</span>
                            ),
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  )
}

/** 'risk_summary:formation=Barail:offset_bin=500.0' -> 'Barail history, 500 m below top';
 *  'gas_mean:+0.728' -> 'gas mean +0.73' (SHAP-style feature contribution). */
function prettyReason(s: string, t: TFn): string {
  const rs = s.match(/^risk_summary:formation=([^:]+):offset_bin=([\d.]+)/)
  if (rs) return t('risk.reasonHistory', { f: rs[1], n: Math.round(Number(rs[2])) })
  const pr = s.match(/^precedent:([^:]+):weight=([\d.]+)(?::hours_lost=([\d.]+))?/)
  if (pr) return t('risk.reasonPrecedent', { w: pr[1], x: Number(pr[2]).toFixed(2) }) + (pr[3] ? t('risk.reasonNpt', { h: Number(pr[3]).toFixed(1) }) : '')
  const ct = s.match(/^(\d+) offset events? in (\d+) wells? \((.*)\)$/)
  if (ct) return t('risk.reasonCount', { n: ct[1], m: ct[2], list: ct[3] })
  const kv = s.match(/^([a-z_]+):([+-]?[\d.]+)$/i)
  if (kv) return `${kv[1].replace(/_/g, ' ')} ${Number(kv[2]) > 0 ? '+' : ''}${Number(kv[2]).toFixed(2)}`
  return s.replace(/_/g, ' ')
}
