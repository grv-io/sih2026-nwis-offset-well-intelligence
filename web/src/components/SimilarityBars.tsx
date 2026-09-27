import { DIMENSIONS } from '../lib/hazards'
import { useT } from '../i18n'

export interface SimilarityBarsProps {
  dimensions: Record<string, number>
  weights?: Record<string, number>
  unavailable?: string[]
  compact?: boolean
}

// Five tiny bars, one per similarity dimension (nwis/geo/nearby.py). A dimension
// the backend dropped (honest degradation, A1) renders as a muted "n/a" slot,
// never as an empty bar that would read as "zero similarity".
export function SimilarityBars({ dimensions, weights, unavailable = [], compact = false }: SimilarityBarsProps) {
  const { t } = useT()
  return (
    <div className="flex items-end gap-1.5" data-testid="similarity-bars">
      {DIMENSIONS.map((d) => {
        const na = unavailable.includes(d.key) || dimensions[d.key] === undefined
        const v = na ? 0 : Math.max(0, Math.min(1, dimensions[d.key]))
        const w = weights?.[d.key]
        const label = t(`dim.${d.key}`)
        const title = na
          ? t('dim.naTitle', { label })
          : t('dim.valueTitle', { label, pct: (v * 100).toFixed(0) }) + (w !== undefined ? t('dim.weight', { w: (w * 100).toFixed(0) }) : '')
        return (
          <div key={d.key} className="flex flex-col items-center gap-0.5" title={title} data-dim={d.key} data-na={na || undefined}>
            {na ? (
              <span className={'flex items-center justify-center text-faint italic ' + (compact ? 'h-4 w-5 text-[10px]' : 'h-5 w-6 text-micro')}>
                {t('ui.na')}
              </span>
            ) : (
              <span className={'relative block bg-s3 rounded-sm overflow-hidden ' + (compact ? 'h-4 w-5' : 'h-5 w-6')}>
                <span
                  className="absolute bottom-0 left-0 right-0 bg-ink2 rounded-t-[1px]"
                  style={{ height: `${Math.max(v * 100, v > 0 ? 8 : 0)}%` }}
                  data-testid="bar-fill"
                />
              </span>
            )}
            {!compact && <span className="text-[10px] leading-[12px] text-faint whitespace-nowrap">{t(`dim.${d.key}.short`)}</span>}
          </div>
        )
      })}
    </div>
  )
}
