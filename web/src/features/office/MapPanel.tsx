import { useEffect, useMemo } from 'react'
import { Circle, CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { LatLngBoundsExpression } from 'leaflet'
import { FAMILY } from '../../lib/hazards'
import { MAP_COLORS } from '../../lib/theme'
import { useEventLabel, useT, type TFn } from '../../i18n'
import type { Basin, HazardFamily, MapData, MapWell } from '../../lib/types'

const TILE_URL = import.meta.env.VITE_TILE_URL || 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTR = import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; OpenStreetMap contributors'

// Fallback center (no active well yet) per basin: Upper Assam vs. Volve, North Sea.
const FALLBACK_CENTER: Record<Basin, [number, number]> = {
  assam: [27.4, 95.2],
  volve: [58.44, 1.89],
}

// fitBounds without animation: an animated zoom that is still running when the map
// unmounts (well/basin switch, route change) makes Leaflet throw on the removed pane.
function FitOnce({ bounds }: { bounds: LatLngBoundsExpression | null }) {
  const map = useMap()
  useEffect(() => {
    if (bounds) map.fitBounds(bounds, { padding: [24, 24], animate: false })
    // fit only when the well set first arrives
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bounds === null])
  return null
}

function FitRing({ ring }: { ring: [number, number][] }) {
  const map = useMap()
  const key = ring.length ? ring.filter((_, i) => i % 16 === 0).map((p) => p.map((v) => v.toFixed(4)).join(',')).join(';') : ''
  useEffect(() => {
    if (ring.length) map.fitBounds(ring as LatLngBoundsExpression, { paddingTopLeft: [24, 24], paddingBottomRight: [24, 56], maxZoom: 13, animate: false })
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

function tooltipBody(w: MapWell, ev: (t: string) => string, t: TFn) {
  const top = Object.entries(w.event_counts).sort((a, b) => b[1] - a[1]).slice(0, 3)
  return (
    <div className="font-sans text-xs leading-5">
      <div className="font-mono font-semibold">{w.well_id} · {w.field}</div>
      <div className="text-dim">{w.trajectory_type}, TD {Math.round(w.td_md_m)} m</div>
      {top.length ? top.map(([k, n]) => <div key={k}>{ev(k)} × {n}</div>) : <div className="text-dim">{t('map.noEvents')}</div>}
    </div>
  )
}

export function MapPanel({ data, basin = 'assam', onSelect, focusId }: {
  data: MapData
  basin?: Basin
  onSelect: (id: string) => void
  focusId?: string | null
}) {
  const { t } = useT()
  const ev = useEventLabel()
  const active = data.wells.find((w) => w.well_id === data.active_well_id)
  const candidateIds = useMemo(() => new Set(data.candidates.map((c) => c.well_id)), [data.candidates])
  const bounds = useMemo<LatLngBoundsExpression | null>(() => {
    if (!data.wells.length) return null
    const lats = data.wells.map((w) => w.lat)
    const lons = data.wells.map((w) => w.lon)
    return [[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]]
  }, [data.wells])

  // Draw order: plain wells, then candidates, then the active well on top.
  const ordered = [...data.wells].sort((a, b) => rank(a) - rank(b))
  function rank(w: MapWell) {
    return w.well_id === data.active_well_id ? 2 : candidateIds.has(w.well_id) ? 1 : 0
  }

  return (
    <div className="relative h-full w-full" data-testid="office-map">
      <MapContainer
        className="nwis-dark-tiles h-full w-full"
        center={active ? [active.lat, active.lon] : FALLBACK_CENTER[basin]}
        zoom={10}
        zoomControl
        attributionControl
        preferCanvas
      >
        <TileLayer url={TILE_URL} attribution={TILE_ATTR} />
        {!active && <FitOnce bounds={bounds} />}
        <FitRing ring={data.ring} />
        {active && (
          <Circle
            center={[active.lat, active.lon]}
            radius={data.radius_km * 1000}
            pathOptions={{ color: MAP_COLORS.ring, weight: 1.5, dashArray: '6 6', fillColor: MAP_COLORS.ring, fillOpacity: 0.05 }}
            interactive={false}
          />
        )}
        {ordered.map((w) => {
          const fam = (w.dominant_family ?? null) as HazardFamily | null
          const color = fam ? FAMILY[fam].color : MAP_COLORS.noFamily
          const isActive = w.well_id === data.active_well_id
          const isCand = candidateIds.has(w.well_id)
          const isFocus = w.well_id === focusId
          return (
            <CircleMarker
              key={`${w.well_id}-${isActive}-${isCand}-${isFocus}-${fam}`}
              center={[w.lat, w.lon]}
              radius={isActive ? 9 : isCand ? 7 : 5}
              pathOptions={{
                color: isActive ? MAP_COLORS.activeEdge : isFocus ? MAP_COLORS.focus : isCand ? MAP_COLORS.candEdge : MAP_COLORS.plainEdge,
                weight: isActive ? 3 : isFocus ? 3 : isCand ? 1.5 : 1,
                fillColor: color,
                fillOpacity: fam ? (isActive || isCand ? 1 : 0.75) : 0.15,
              }}
              eventHandlers={{ click: () => onSelect(w.well_id) }}
            >
              <Tooltip
                direction="top"
                offset={[0, -8]}
                permanent={isActive || isCand}
                className={'nwis-tip' + (isActive ? ' nwis-tip-active' : '')}
              >
                {isActive || isCand ? w.well_id : tooltipBody(w, ev, t)}
              </Tooltip>
            </CircleMarker>
          )
        })}
      </MapContainer>
      <Legend />
    </div>
  )
}

function Legend() {
  // One compact strip under the map so it never covers a well.
  const { t } = useT()
  return (
    <div
      className="absolute left-0 right-0 bottom-0 z-[500] flex items-center gap-x-3 h-7 px-3 overflow-hidden whitespace-nowrap border-t border-line bg-s1/95 text-micro text-ink2"
      aria-label={t('map.legendAria')}
    >
      <span className="label !text-[10.5px]">{t('map.legend')}</span>
      {(Object.keys(FAMILY) as HazardFamily[]).map((k) => (
        <span key={k} className="flex items-center gap-1.5" title={t(`family.${k}.members`)}>
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: FAMILY[k].color }} />
          {t(`family.${k}`)}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-full border border-dim" />
        {t('map.legendNone')}
      </span>
    </div>
  )
}
