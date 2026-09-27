import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ClipboardCheck, FileText, X } from 'lucide-react'
import { api } from '../../lib/api'
import { fmtNum } from '../../lib/format'
import type { DrillingEvent } from '../../lib/types'
import { useEvidence } from '../../components/Evidence'
import { Empty, ErrorNote, Loading, Segmented } from '../../components/ui'
import { useEventLabel, useT } from '../../i18n'

function Confidence({ v, threshold }: { v: number; threshold: number }) {
  const { t } = useT()
  const low = v < threshold
  return (
    <div className="flex items-center gap-2" title={t('review.confTitle', { v: v.toFixed(2), th: threshold })}>
      <span className="relative h-1.5 w-14 rounded-full bg-s3 overflow-hidden">
        <span className={'absolute inset-y-0 left-0 ' + (low ? 'bg-high' : 'bg-ink2')} style={{ width: `${v * 100}%` }} />
        <span className="absolute inset-y-0 w-px bg-ink/60" style={{ left: `${threshold * 100}%` }} />
      </span>
      <span className={'num text-xs ' + (low ? 'text-high font-semibold' : 'text-ink2')}>{v.toFixed(2)}</span>
    </div>
  )
}

export function ReviewPanel() {
  const { t } = useT()
  const eventLabel = useEventLabel()
  const qc = useQueryClient()
  const open = useEvidence()
  const [view, setView] = useState<'pending' | 'all'>('pending')
  const queue = useQuery({ queryKey: ['review'], queryFn: api.reviewQueue, refetchInterval: 15_000 })
  const all = useQuery({
    queryKey: ['events', 'extracted-all'],
    queryFn: () => api.events({ source: 'extracted' }),
    enabled: view === 'all',
    refetchInterval: 15_000,
  })
  // First load: if nothing is pending, open on the full extracted list rather than an empty table.
  const initialised = useRef(false)
  useEffect(() => {
    if (!initialised.current && queue.data) {
      initialised.current = true
      if (queue.data.counters.pending === 0) setView('all')
    }
  }, [queue.data])
  const [done, setDone] = useState<Record<string, 'approved' | 'rejected'>>({})

  const act = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: 'approve' | 'reject' }) => (kind === 'approve' ? api.approve(id) : api.reject(id)),
    onSuccess: (_d, { id, kind }) => {
      setDone((m) => ({ ...m, [id]: kind === 'approve' ? 'approved' : 'rejected' }))
      qc.invalidateQueries({ queryKey: ['review'] })
      qc.invalidateQueries({ queryKey: ['events'] })
      qc.invalidateQueries({ queryKey: ['map'] })
      qc.invalidateQueries({ queryKey: ['corr'] })
    },
  })

  const threshold = queue.data?.threshold ?? 0.6
  const rows: DrillingEvent[] =
    view === 'pending'
      ? queue.data?.items ?? []
      : [...(all.data ?? [])].sort((a, b) => a.extraction_confidence - b.extraction_confidence)
  const c = queue.data?.counters

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex flex-wrap items-center gap-6 px-4 py-3 border-b border-rule">
        {[
          { id: 'extracted', k: t('review.extracted'), v: c?.extracted, hint: t('review.extractedHint') },
          { id: 'reviewed', k: t('review.reviewed'), v: c?.reviewed, hint: t('review.reviewedHint') },
          { id: 'pending', k: t('review.pending'), v: c?.pending, hint: t('review.pendingHint', { th: threshold }), tone: (c?.pending ?? 0) > 0 },
        ].map((s) => (
          <div key={s.id} className="kv" title={s.hint} data-testid={`review-counter-${s.id}`}>
            <span className="label">{s.k}</span>
            <span className={'num text-xl font-semibold ' + (s.tone ? 'text-high' : 'text-ink')}>{s.v ?? '—'}</span>
          </div>
        ))}
        <div className="ml-auto">
          <Segmented
            label={t('review.rows')}
            value={view}
            onChange={setView}
            options={[
              { value: 'pending', label: `${t('review.needs')}${c ? ` (${c.pending})` : ''}` },
              { value: 'all', label: t('review.all') },
            ]}
          />
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto">
        {queue.isError ? (
          <ErrorNote error={queue.error} what={t('review.what')} />
        ) : queue.isLoading || (view === 'all' && all.isLoading) ? (
          <Loading />
        ) : rows.length === 0 ? (
          <Empty icon={<ClipboardCheck size={28} />} title={view === 'pending' ? t('review.emptyPendingTitle') : t('review.emptyAllTitle')}>
            {view === 'pending' ? t('review.emptyPendingBody', { th: threshold.toFixed(2) }) : t('review.emptyAllBody')}
          </Empty>
        ) : (
          <table className="w-full border-separate border-spacing-0" data-testid="review-table">
            <thead className="table-head">
              <tr>
                <th>{t('col.well')}</th>
                <th>{t('col.event')}</th>
                <th className="text-right">{t('col.md')}</th>
                <th>{t('col.formation')}</th>
                <th>{t('col.confidence')}</th>
                <th>{t('col.evidence')}</th>
                <th className="text-right">{t('col.decision')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => {
                const low = e.extraction_confidence < threshold && !e.reviewed_by_human
                const state = done[e.event_id] ?? (e.reviewed_by_human ? 'approved' : undefined)
                return (
                  <tr
                    key={e.event_id}
                    className={'table-row ' + (low && !state ? 'bg-high/[0.07] shadow-[inset_3px_0_0_rgb(var(--high))]' : '') + (state === 'rejected' ? ' opacity-40 line-through' : '')}
                  >
                    <td className="font-mono whitespace-nowrap">{e.well_id}</td>
                    <td>
                      <div className="text-ink whitespace-nowrap">{eventLabel(e.event_type)}</div>
                      {low && !state && <div className="text-micro text-high">{e.reason ?? t('review.lowConfidence')}</div>}
                    </td>
                    <td className="num text-right">{fmtNum(e.depth_md_m)}</td>
                    <td className={e.formation ? '' : 'text-faint'}>{e.formation ?? t('review.unresolved')}</td>
                    <td><Confidence v={e.extraction_confidence} threshold={threshold} /></td>
                    <td className="max-w-[280px]">
                      <button
                        className="flex items-start gap-1.5 text-left text-xs text-ink2 hover:text-accent"
                        onClick={() =>
                          open({ ref: e.source_document_id, page: pageOf(e.source_page_ref), quote: e.free_text, title: t('review.evidenceTitle') })
                        }
                        title={t('review.openEvidence')}
                      >
                        <FileText size={12} className="mt-0.5 shrink-0" />
                        <span className="line-clamp-2">{e.free_text ? `“${e.free_text}”` : e.source_page_ref}</span>
                      </button>
                    </td>
                    <td className="text-right whitespace-nowrap">
                      {state ? (
                        <span className={'text-xs font-semibold uppercase tracking-[0.06em] ' + (state === 'approved' ? 'text-ok' : 'text-dim')}>
                          {state === 'approved' ? t('review.approved') : t('review.rejected')}
                        </span>
                      ) : (
                        <span className="inline-flex gap-1.5">
                          <button
                            className="btn btn-sm"
                            onClick={() => act.mutate({ id: e.event_id, kind: 'approve' })}
                            disabled={act.isPending}
                            aria-label={t('review.approveAria', { id: e.event_id })}
                          >
                            <Check size={13} /> {t('review.approve')}
                          </button>
                          <button
                            className="btn btn-sm"
                            onClick={() => act.mutate({ id: e.event_id, kind: 'reject' })}
                            disabled={act.isPending}
                            aria-label={t('review.rejectAria', { id: e.event_id })}
                          >
                            <X size={13} /> {t('review.reject')}
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function pageOf(ref: string) {
  const m = ref.match(/#p(\d+)/)
  return m ? parseInt(m[1], 10) : 1
}
