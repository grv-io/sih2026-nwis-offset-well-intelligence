import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Layers } from 'lucide-react'
import { StaticMissError, api } from '../../lib/api'
import { fmtNum } from '../../lib/format'
import type { OffsetCandidate, Source } from '../../lib/types'
import { useElementSize } from '../../lib/useElementSize'
import { Empty, ErrorNote, Loading, Segmented } from '../../components/ui'
import { useT } from '../../i18n'

const PlotlyFigure = lazy(() => import('../../components/PlotlyFigure'))

const FORMATIONS = ['Alluvium', 'Dhekiajuli', 'Namsang', 'Girujan', 'Tipam', 'Barail', 'Kopili', 'Sylhet', 'Basement']
const MAX_WELLS = 6

function DipReadout({ formation, onChange }: { formation: string; onChange: (f: string) => void }) {
  const { t } = useT()
  const q = useQuery({ queryKey: ['dip', formation], queryFn: () => api.dip(formation) })
  const d = q.data
  const fallback = d && d.n < 3
  return (
    <div className="flex items-center gap-4 flex-wrap" data-testid="dip-readout">
      <label className="flex items-center gap-2">
        <span className="label">{t('corr.dip')}</span>
        <select
          value={formation}
          onChange={(e) => onChange(e.target.value)}
          className="input h-7 py-0 pr-7 text-xs"
          aria-label={t('corr.dipAria')}
        >
          {FORMATIONS.map((f) => (
            <option key={f}>{f}</option>
          ))}
        </select>
      </label>
      {q.isLoading && <span className="text-xs text-dim">{t('corr.fitting')}</span>}
      {d && (
        <div className="flex items-baseline gap-4 text-sm">
          <span title={t('corr.dipTitle')}>
            <span className="num text-ink font-semibold">{fmtNum(d.theta_deg, 2)}°</span>
            <span className="text-xs text-dim ml-1">{t('corr.dipWord')}</span>
          </span>
          <span title={t('corr.azimuthTitle')}>
            <span className="num text-ink font-semibold">{fmtNum(d.azimuth_deg, 0).padStart(3, '0')}°</span>
            <span className="text-xs text-dim ml-1">{t('corr.azimuth')}</span>
          </span>
          <span title={t('corr.rmseTitle')}>
            <span className="num text-ink2">{d.rmse_m === null ? '—' : fmtNum(d.rmse_m, 1) + ' m'}</span>
            <span className="text-xs text-dim ml-1">RMSE</span>
          </span>
          <span className="num text-xs text-dim">{t('corr.nWells', { n: d.n })}</span>
          {fallback && <span className="chip text-dim">{t('corr.dipDefault')}</span>}
        </div>
      )}
    </div>
  )
}

export function CorrelationPanel({ activeId, candidates, source }: {
  activeId: string
  candidates: OffsetCandidate[]
  source: Source
}) {
  const { t } = useT()
  const [mode, setMode] = useState<'tvd' | 'normalised'>('normalised')
  const [formation, setFormation] = useState('Girujan')
  const defaultIds = useMemo(() => [activeId, ...candidates.slice(0, 4).map((c) => c.well_id)], [activeId, candidates])
  const [ids, setIds] = useState<string[]>(defaultIds)
  useEffect(() => setIds(defaultIds), [defaultIds])

  const [boxRef, size] = useElementSize<HTMLDivElement>()
  const q = useQuery({
    queryKey: ['corr', ids.join(','), mode, source],
    queryFn: () => api.correlationFigure(ids, mode, source),
    enabled: ids.length > 0,
    placeholderData: (prev) => prev,
  })

  const toggle = (id: string) =>
    setIds((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= MAX_WELLS ? cur : [...cur, id]))

  return (
    <div className="h-full flex flex-col min-h-0">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-rule">
        <Segmented
          label={t('corr.depthRef')}
          value={mode}
          onChange={setMode}
          options={[
            { value: 'normalised', label: t('corr.aligned'), title: t('corr.alignedTitle') },
            { value: 'tvd', label: t('corr.tvd') },
          ]}
        />
        <div className="flex flex-wrap items-center gap-1.5" aria-label={t('corr.wellsAria')}>
          <span className="chip border-accent/60 text-accent font-mono">{activeId}</span>
          {candidates.slice(0, 8).map((c) => {
            const on = ids.includes(c.well_id)
            return (
              <button
                key={c.well_id}
                className={'chip font-mono ' + (on ? 'text-ink border-ink2/50' : 'text-faint border-dashed hover:text-ink2')}
                aria-pressed={on}
                onClick={() => toggle(c.well_id)}
                title={on ? t('corr.remove') : t('corr.add')}
              >
                {c.well_id}
              </button>
            )
          })}
        </div>
        <span className="ml-auto text-xs text-dim num" title={t('corr.eventsTitle')}>{q.data ? t('corr.eventsMeta', { n: q.data.n_events }) : ''}</span>
      </div>
      <div className="px-4 py-2 border-b border-rule">
        <DipReadout formation={formation} onChange={setFormation} />
      </div>
      <div ref={boxRef} className="flex-1 min-h-[360px] relative">
        {q.isError ? (
          q.error instanceof StaticMissError ? (
            <Empty icon={<Layers />} title={t('static.corrMissTitle')}>
              <span data-testid="corr-static-miss">{t('static.corrMiss')}</span>
            </Empty>
          ) : (
            <ErrorNote error={q.error} what={t('corr.what')} />
          )
        ) : !q.data ? (
          <Loading label={t('corr.building')} />
        ) : (
          <>
            {q.data.n_events === 0 && (
              <div className="absolute top-2 right-4 z-10 text-xs text-dim bg-s1/90 px-2 py-1 rounded border border-line">
                {t('corr.noEvents', { src: source === 'truth' ? t('corr.srcTruth') : t('corr.srcExtracted') })}
              </div>
            )}
            <Suspense fallback={<Loading label={t('corr.engine')} />}>
              {size.height > 0 ? (
                <PlotlyFigure figure={q.data.figure} height={Math.max(360, size.height)} yCategories={mode === 'normalised' ? FORMATIONS : undefined} />
              ) : (
                <Empty icon={<Layers />} title={t('corr.title')} />
              )}
            </Suspense>
          </>
        )}
      </div>
    </div>
  )
}
