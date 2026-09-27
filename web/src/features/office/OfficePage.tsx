import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BookOpen, ClipboardCheck, Gauge, Layers } from 'lucide-react'
import { api } from '../../lib/api'
import { fmtNum } from '../../lib/format'
import { WellPicker } from '../../components/WellPicker'
import { Empty, ErrorNote, Loading, Panel, Segmented, Switch } from '../../components/ui'
import { MapPanel } from './MapPanel'
import { OffsetTable } from './OffsetTable'
import { CorrelationPanel } from './CorrelationPanel'
import { MemoryPanel } from './MemoryPanel'
import { RiskPanel } from './RiskPanel'
import { ReviewPanel } from './ReviewPanel'
import { useOfficeState, TOPS_ONLY_TABS, type OfficeTab } from './useOfficeState'
import type { Basin } from '../../lib/types'
import { useT, type MsgKey } from '../../i18n'

const TABS: { id: OfficeTab; label: MsgKey; icon: typeof Layers }[] = [
  { id: 'correlation', label: 'tab.correlation', icon: Layers },
  { id: 'memory', label: 'tab.memory', icon: BookOpen },
  { id: 'risk', label: 'tab.risk', icon: Gauge },
  { id: 'review', label: 'tab.review', icon: ClipboardCheck },
]

const BASIN_OPTIONS: { value: Basin; label: MsgKey; title: MsgKey }[] = [
  { value: 'assam', label: 'basin.assam', title: 'basin.assamTitle' },
  { value: 'volve', label: 'basin.volve', title: 'basin.volveTitle' },
]

export default function OfficePage() {
  const { t, lang } = useT()
  const s = useOfficeState()
  const [focus, setFocus] = useState<string | null>(null)
  // /wells is Assam-only (Phase 10 kept it that way — the Volve wells the
  // picker needs for that basin come back on /map/data, which is basin-aware).
  const wells = useQuery({ queryKey: ['wells'], queryFn: api.wells, staleTime: Infinity, enabled: s.basin === 'assam' })
  const summary = useQuery({ queryKey: ['summary'], queryFn: api.summary, refetchInterval: 20_000 })
  const review = useQuery({ queryKey: ['review'], queryFn: api.reviewQueue, refetchInterval: 15_000 })
  const map = useQuery({
    queryKey: ['map', s.basin, s.wellId, s.radiusKm, s.source],
    queryFn: () => api.mapData(s.wellId, s.radiusKm, s.source, s.basin),
    placeholderData: (p) => p,
  })
  const pickerWells = s.basin === 'volve' ? map.data?.wells ?? [] : wells.data ?? []
  const well = pickerWells.find((w) => w.well_id === s.wellId)
  const sm = summary.data
  const tabsDisabled = s.basin === 'volve'

  return (
    <div className="h-full flex flex-col min-h-0" data-testid="office-page">
      {/* Context bar: what every panel below is about */}
      <div className="shrink-0 flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2 bg-s1 border-b border-line">
        <Segmented
          value={s.basin}
          options={BASIN_OPTIONS.map((o) => ({ value: o.value, label: t(o.label), title: t(o.title) }))}
          onChange={(b) => (setFocus(null), s.setBasin(b))}
          label={t('office.basin')}
        />
        <WellPicker wells={pickerWells} value={s.wellId} onChange={(id) => (setFocus(null), s.set({ well: id, md: null }))} label={t('picker.activeWell')} wide />
        <label className="flex items-center gap-3">
          <span className="label">{t('office.radius')}</span>
          <input
            type="range"
            min={1}
            max={10}
            step={0.5}
            value={s.radiusKm}
            onChange={(e) => s.set({ r: e.target.value })}
            className="w-[140px] accent-[rgb(var(--accent))]"
            aria-label={t('office.radiusAria')}
            data-testid="radius-slider"
          />
          <span className="num text-sm font-semibold text-ink w-[52px]">{fmtNum(s.radiusKm, 1)} km</span>
        </label>
        <Switch
          checked={s.source === 'truth'}
          onChange={(v) => s.set({ src: v ? 'truth' : null })}
          label={<span>{t('office.groundTruth')} <span className="text-dim">{t('office.groundTruthNote')}</span></span>}
          hint={t('office.groundTruthHint')}
        />
        {s.source === 'truth' && (
          <span className="chip border-accent/50 text-accent" data-testid="truth-banner">{t('office.truthBanner')}</span>
        )}
        <div className="ml-auto flex items-center gap-5 text-xs text-dim">
          {well && (
            <span className="hidden wide:inline">
              <span className="font-mono text-ink2">{well.well_id}</span> · {well.field} · {well.trajectory_type} · TD{' '}
              <span className="num text-ink2">{fmtNum(well.td_md_m)} m</span>
            </span>
          )}
          {sm && (
            <span title={t('office.ingestTitle')} className="num">
              <span className="text-ink2 font-semibold">{sm.events_extracted}</span> {t('office.extractedEvents')} ·{' '}
              <span className="text-ink2">{sm.documents_ingested}</span>/{sm.documents} {t('office.docsIngested')}
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 grid gap-2 p-2 grid-cols-1 tab:grid-cols-[minmax(400px,5fr)_7fr]">
        {/* Left: spatial context, always visible */}
        <div className="min-h-0 grid gap-2 grid-rows-[minmax(300px,1.25fr)_minmax(220px,1fr)]">
          <Panel
            title={t('map.title')}
            meta={map.data ? t('map.meta', { wells: map.data.wells.length, cands: map.data.candidates.length, km: fmtNum(s.radiusKm, 1) }) : undefined}
            bodyClass="relative"
          >
            {map.isError ? (
              <ErrorNote error={map.error} what={t('map.what')} />
            ) : map.data ? (
              // key={basin}: force a remount so react-leaflet recomputes its
              // initial center/bounds for the new basin (Volve ~58.44N 1.89E vs
              // Assam ~27.4N 95.2E) instead of keeping the old view's pan/zoom.
              <MapPanel key={s.basin} data={map.data} basin={s.basin} onSelect={(id) => (setFocus(null), s.set({ well: id, md: null }))} focusId={focus} />
            ) : (
              <Loading label={t('map.loading')} />
            )}
          </Panel>
          <Panel title={t('offsets.title')} meta={s.basin === 'assam' ? t('offsets.meta') : undefined}>
            {s.basin === 'volve' ? (
              <Empty title={t('offsets.volveTitle')}>
                {lang === 'hi' ? t('offsets.volveBody') : map.data?.note ?? t('offsets.volveBody')}
              </Empty>
            ) : map.data ? (
              <OffsetTable
                candidates={map.data.candidates}
                wells={map.data.wells}
                radiusKm={s.radiusKm}
                focusId={focus}
                onFocus={setFocus}
                onMakeActive={(id) => (setFocus(null), s.set({ well: id, md: null }))}
              />
            ) : (
              <Loading />
            )}
          </Panel>
        </div>

        {/* Right: analysis workspace */}
        <section className="panel min-h-[560px] tab:min-h-0 flex flex-col">
          <div role="tablist" aria-label={t('tabs.aria')} className="shrink-0 flex items-stretch h-11 border-b border-rule px-2">
            {TABS.map((tab) => {
              const on = tab.id === s.tab
              const Icon = tab.icon
              const badge = tab.id === 'review' ? review.data?.counters.pending : undefined
              const disabled = tabsDisabled && TOPS_ONLY_TABS.includes(tab.id)
              return (
                <button
                  key={tab.id}
                  role="tab"
                  aria-selected={on}
                  aria-disabled={disabled}
                  disabled={disabled}
                  title={disabled ? t('tab.needsTops') : undefined}
                  onClick={() => !disabled && s.set({ tab: tab.id })}
                  className={
                    'flex items-center gap-2 px-3 text-sm border-b-2 -mb-px transition-colors ' +
                    (disabled
                      ? 'border-transparent text-faint cursor-not-allowed'
                      : on
                        ? 'border-accent text-ink font-semibold'
                        : 'border-transparent text-dim hover:text-ink')
                  }
                  data-testid={`tab-${tab.id}`}
                >
                  <Icon size={15} />
                  {t(tab.label)}
                  {badge ? <span className="num h-5 min-w-5 px-1.5 rounded-sm bg-high/15 text-high text-micro font-semibold flex items-center" title={t('tab.pendingTitle', { n: badge })}>{badge}</span> : null}
                </button>
              )
            })}
          </div>
          <div className="flex-1 min-h-0" role="tabpanel">
            {s.tab === 'correlation' &&
              (map.data ? (
                <CorrelationPanel activeId={s.wellId} candidates={map.data.candidates} source={s.source} />
              ) : (
                <Loading />
              ))}
            {s.tab === 'memory' && <MemoryPanel basin={s.basin} />}
            {s.tab === 'risk' && <RiskPanel well={well} source={s.source} bitMd={s.bitMd} onBitMd={(v) => s.set({ md: v })} />}
            {s.tab === 'review' && <ReviewPanel />}
          </div>
        </section>
      </div>
    </div>
  )
}
