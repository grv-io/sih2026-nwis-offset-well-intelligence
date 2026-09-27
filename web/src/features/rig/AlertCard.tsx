import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, FileSearch, Lightbulb } from 'lucide-react'
import { api } from '../../lib/api'
import { parseCitation, type Citation } from '../../lib/citations'
import { eventLabel, normaliseSeverity, SEVERITY_META } from '../../lib/hazards'
import { fmtNum, fmtRigClock } from '../../lib/format'
import type { Alert, DrillingEvent } from '../../lib/types'
import { CitationChip } from '../../components/CitationChip'
import { DocumentViewer, Drawer } from '../../components/Evidence'
import { SeverityBadge } from '../../components/SeverityBadge'
import { prettyAlertMessage, RecommendationText } from './text'
import { tMaybe, useEventLabel, useT } from '../../i18n'

function usePrecedents(ids: string[]) {
  return useQuery({
    queryKey: ['events', 'ids', ids.join(',')],
    queryFn: () => api.events({ ids, source: 'all' }),
    enabled: ids.length > 0,
    staleTime: Infinity,
  })
}

type EvidenceItem = { key: string; title: string; sub: string; ref: string; page: number; quote: string | null; remedy?: string | null }

/** A6: one click from an alert to the offset-well reports that justify it. */
function InvestigateDrawer({ alert, precedents, onClose }: { alert: Alert; precedents: DrillingEvent[]; onClose: () => void }) {
  const { t } = useT()
  const ev = useEventLabel()
  const items = useMemo<EvidenceItem[]>(() => {
    const out: EvidenceItem[] = precedents.map((e) => ({
      key: e.event_id,
      title: `${e.well_id} · ${ev(e.event_type)} @ ${fmtNum(e.depth_md_m)} m`,
      sub: `${e.formation ?? t('ui.formationNa')}${e.report_date ? ' · ' + e.report_date : ''}${e.hours_lost_npt != null ? ` · ${fmtNum(e.hours_lost_npt, 1)} h NPT` : ''}`,
      ref: e.source_document_id,
      page: Number(e.source_page_ref.match(/#p(\d+)/)?.[1] ?? 1),
      quote: e.free_text || null,
      remedy: e.remedy,
    }))
    const seen = new Set(precedents.map((e) => e.source_page_ref))
    alert.citations.map(parseCitation).forEach((c: Citation, i) => {
      if (c.kind !== 'document' || (c.pageRef && seen.has(c.pageRef))) return
      out.push({ key: 'c' + i, title: c.label, sub: t('alert.citedInRec'), ref: c.documentId ?? c.pageRef ?? c.raw, page: c.page, quote: c.quote })
    })
    return out
  }, [alert, precedents, ev, t])
  const [sel, setSel] = useState(0)
  const cur = items[sel]

  return (
    <Drawer title={t('alert.drawerTitle', { ev: ev(alert.hazard, { bilingual: true }), md: fmtNum(alert.depth_md_m) })} onClose={onClose} width={1040}>
      <div className="flex-1 min-h-0 grid grid-cols-[minmax(260px,340px)_1fr]">
        <ol className="border-r border-rule overflow-auto" aria-label={t('alert.evidenceAria')}>
          {items.length === 0 && <li className="p-4 text-sm text-dim">{t('alert.sensorOnly')}</li>}
          {items.map((it, i) => (
            <li key={it.key}>
              <button
                onClick={() => setSel(i)}
                className={'w-full text-left px-4 py-3 border-b border-rule ' + (i === sel ? 'bg-s3 shadow-[inset_3px_0_0_rgb(var(--accent))]' : 'hover:bg-s2')}
              >
                <div className="text-sm text-ink font-medium">{it.title}</div>
                <div className="text-xs text-dim mt-0.5">{it.sub}</div>
                {it.remedy && (
                  <div className="text-xs text-ink2 mt-1.5 leading-5">
                    <span className="label !text-[10px] mr-1">{t('alert.remedy')}</span>
                    {it.remedy}
                  </div>
                )}
              </button>
            </li>
          ))}
        </ol>
        <div className="min-h-0 flex flex-col">
          {cur ? (
            <DocumentViewer key={cur.key} req={{ ref: cur.ref, page: cur.page, quote: cur.quote, highlight: [eventLabel(alert.hazard), alert.hazard.replace('_', ' ')] }} /* English on purpose: reports are English */ />
          ) : null}
        </div>
      </div>
    </Drawer>
  )
}

export function AlertCard({ alert, isNew, onAck, acking }: { alert: Alert; isNew: boolean; onAck: () => void; acking: boolean }) {
  const { t } = useT()
  const ev = useEventLabel()
  const [investigating, setInvestigating] = useState(false)
  const [docCite, setDocCite] = useState<Citation | null>(null)
  const sev = normaliseSeverity(alert.severity)
  const meta = SEVERITY_META[sev]
  const rule = { label: tMaybe(t, `rule.${alert.rule}`, alert.rule), hint: tMaybe(t, `rule.${alert.rule}.hint`, '') }
  const prec = usePrecedents(alert.precedent_event_ids)
  const precedentWells = useMemo(() => {
    const m = new Map<string, DrillingEvent[]>()
    for (const e of prec.data ?? []) m.set(e.well_id, [...(m.get(e.well_id) ?? []), e])
    return [...m.entries()]
  }, [prec.data])
  const acked = alert.acknowledged

  return (
    <article
      className={'relative rounded border bg-s1 ' + (acked ? 'border-line opacity-70' : 'border-line') + (isNew ? ' nwis-arrive' : '')}
      style={{ boxShadow: `inset 4px 0 0 rgb(var(${meta.rgbVar})${acked ? ' / 0.4' : ''})` }}
      data-testid="alert-card"
      data-severity={sev}
      aria-label={t('alert.aria', { label: t(`sev.${sev}`), msg: alert.message })}
    >
      <header className="flex flex-wrap items-center gap-3 pl-5 pr-4 pt-3">
        <SeverityBadge severity={sev} size="lg" showPriority muted={acked} />
        <span className="text-lg font-semibold text-ink">{ev(alert.hazard, { bilingual: true })}</span>
        <span className="chip" title={rule.hint}>{rule.label}</span>
        {acked && <span className="chip text-ok border-ok/40"><Check size={12} /> {t('alert.acknowledged')}</span>}
        <span className="ml-auto num text-sm text-dim">
          @ <span className="text-ink2 font-semibold">{fmtNum(alert.depth_md_m)} m</span>
          {alert.formation && <> · {alert.formation}</>} · {fmtRigClock(alert.t_s)}
        </span>
      </header>

      <p lang="en" className="pl-5 pr-4 mt-2 text-[17px] leading-7 text-ink" title={alert.message}>{prettyAlertMessage(alert.message)}</p>

      {precedentWells.length > 0 && (
        <div className="pl-5 pr-4 mt-3 flex flex-wrap items-center gap-2">
          <span className="label">{t('alert.precedentWells')}</span>
          {precedentWells.map(([w, evs]) => (
            <span key={w} className="chip font-mono text-ink" title={evs.map((e) => `${ev(e.event_type)} @ ${fmtNum(e.depth_md_m)} m, ${e.source_page_ref}`).join('\n')}>
              {w} <span className="num text-faint">×{evs.length}</span>
            </span>
          ))}
        </div>
      )}

      {alert.recommendation && (
        <div className="mx-5 mt-3 rounded-sm border border-rule bg-s2 px-3 py-2.5 flex gap-2.5">
          <Lightbulb size={16} className="text-dim mt-1 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="label mb-0.5">{t('alert.recommendation')}</div>
            <RecommendationText text={alert.recommendation} />
          </div>
        </div>
      )}

      {alert.citations.length > 0 && (
        <div className="pl-5 pr-4 mt-3 flex flex-wrap items-center gap-1.5">
          <span className="label mr-1">{t('alert.sources')}</span>
          {alert.citations.slice(0, 6).map((c, i) => (
            <CitationChip key={i} citation={c} onOpen={setDocCite} />
          ))}
          {alert.citations.length > 6 && <span className="text-xs text-dim">{t('ui.more', { n: alert.citations.length - 6 })}</span>}
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-2 pl-5 pr-4 py-3 mt-3 border-t border-rule">
        {!acked && (
          <button className="btn-primary h-9 px-4" onClick={onAck} disabled={acking} data-testid="ack-button">
            <Check size={15} /> {t('alert.ack')}
          </button>
        )}
        <button className="btn h-9" onClick={() => setInvestigating(true)} data-testid="investigate-button">
          <FileSearch size={15} /> {t('alert.investigate')}
        </button>
        <span className="ml-auto font-mono text-micro text-faint">{alert.alert_id}</span>
      </footer>

      {investigating && <InvestigateDrawer alert={alert} precedents={prec.data ?? []} onClose={() => setInvestigating(false)} />}
      {docCite && (
        <Drawer title={t('alert.citedSource')} onClose={() => setDocCite(null)}>
          <DocumentViewer req={{ ref: docCite.documentId ?? docCite.pageRef ?? docCite.raw, page: docCite.page, quote: docCite.quote }} />
        </Drawer>
      )}
    </article>
  )
}
