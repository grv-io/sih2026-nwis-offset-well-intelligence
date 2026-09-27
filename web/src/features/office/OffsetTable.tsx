import { Crosshair } from 'lucide-react'
import { SimilarityBars } from '../../components/SimilarityBars'
import { Empty } from '../../components/ui'
import { cardinal, fmtNum } from '../../lib/format'
import { useEventLabel, useT } from '../../i18n'
import type { MapWell, OffsetCandidate } from '../../lib/types'

export function OffsetTable({ candidates, wells, radiusKm, focusId, onFocus, onMakeActive }: {
  candidates: OffsetCandidate[]
  wells: MapWell[]
  radiusKm: number
  focusId: string | null
  onFocus: (id: string | null) => void
  onMakeActive: (id: string) => void
}) {
  const { t } = useT()
  const eventLabel = useEventLabel()
  if (!candidates.length) {
    return (
      <Empty title={t('offsets.emptyTitle', { km: radiusKm })}>
        {t('offsets.emptyBody')}
      </Empty>
    )
  }
  const byId = new Map(wells.map((w) => [w.well_id, w]))
  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-separate border-spacing-0" data-testid="offset-table">
        <thead className="table-head">
          <tr>
            <th className="w-8">#</th>
            <th>{t('col.well')}</th>
            <th className="text-right">{t('col.dist')}</th>
            <th className="text-right">{t('col.score')}</th>
            <th title={t('col.similarityTitle')}>{t('col.similarity')}</th>
            <th className="hidden wide:table-cell">{t('col.topEvent')}</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody>
          {candidates.map((c, i) => {
            const w = byId.get(c.well_id)
            const focused = c.well_id === focusId
            return (
              <tr
                key={c.well_id}
                className={'table-row cursor-pointer ' + (focused ? 'bg-s3' : 'hover:bg-s2')}
                onMouseEnter={() => onFocus(c.well_id)}
                onMouseLeave={() => onFocus(null)}
                onClick={() => onFocus(c.well_id)}
              >
                <td className="num text-dim">{i + 1}</td>
                <td className="whitespace-nowrap">
                  <div className="font-mono text-ink leading-5">{c.well_id}</div>
                  <div className="text-micro text-dim leading-4">{w?.trajectory_type}{w?.dominant_event_type ? ' · ' + eventLabel(w.dominant_event_type) : ''}</div>
                </td>
                <td className="num text-right text-ink2 whitespace-nowrap">
                  {fmtNum(c.distance_km, 1)} km <span className="text-faint text-micro">{cardinal(c.bearing_deg)}</span>
                </td>
                <td className="num text-right font-semibold text-ink">{fmtNum(c.score * 100)}</td>
                <td className="py-1">
                  <SimilarityBars dimensions={c.dimensions} weights={c.weights_used} unavailable={c.dimensions_unavailable} />
                </td>
                <td className="hidden wide:table-cell text-xs text-ink2 whitespace-nowrap">
                  {w?.dominant_event_type ? (
                    <>
                      {eventLabel(w.dominant_event_type)}
                      <span className="num text-faint"> ×{w.event_counts[w.dominant_event_type]}</span>
                    </>
                  ) : (
                    <span className="text-faint">{t('ui.none')}</span>
                  )}
                </td>
                <td>
                  <button
                    className="btn-ghost btn-sm"
                    title={t('offsets.makeActive', { id: c.well_id })}
                    aria-label={t('offsets.makeActive', { id: c.well_id })}
                    onClick={(e) => {
                      e.stopPropagation()
                      onMakeActive(c.well_id)
                    }}
                  >
                    <Crosshair size={14} />
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
