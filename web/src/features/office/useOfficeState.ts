import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Basin, Source } from '../../lib/types'

export type OfficeTab = 'correlation' | 'memory' | 'risk' | 'review'
const TABS: OfficeTab[] = ['correlation', 'memory', 'risk', 'review']

// Tabs that need formation tops (similarity ranking / SHAP-over-offsets), which
// only the synthetic Assam basin has (Volve Phase 8: tops not loaded, see
// nwis/external/volve/wells.py docstring).
export const TOPS_ONLY_TABS: OfficeTab[] = ['correlation', 'risk']

export const DEFAULT_WELL: Record<Basin, string> = { assam: 'DUL-005', volve: '15_9-F-12' }

/** Office view state lives in the URL so a link (or a reload mid-demo) restores it. */
export function useOfficeState() {
  const [sp, setSp] = useSearchParams()
  const state = useMemo(() => {
    const r = Number(sp.get('r'))
    const md = Number(sp.get('md'))
    const tab = sp.get('tab') as OfficeTab
    const basin: Basin = sp.get('basin') === 'volve' ? 'volve' : 'assam'
    const resolvedTab = TABS.includes(tab) ? tab : ('correlation' as OfficeTab)
    return {
      basin,
      wellId: sp.get('well') || DEFAULT_WELL[basin],
      radiusKm: r >= 1 && r <= 10 ? r : 5,
      source: (sp.get('src') === 'truth' ? 'truth' : 'extracted') as Source,
      // A basin switch can leave the URL pointing at a tops-only tab that the
      // new basin can't render; fall back to Memory rather than a blank panel.
      tab: basin === 'volve' && TOPS_ONLY_TABS.includes(resolvedTab) ? 'memory' : resolvedTab,
      bitMd: md > 0 ? md : null,
    }
  }, [sp])

  const set = useCallback(
    (patch: Partial<Record<'well' | 'r' | 'src' | 'tab' | 'md' | 'basin', string | number | null>>) => {
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === undefined || v === '') next.delete(k)
            else next.set(k, String(v))
          }
          return next
        },
        { replace: true },
      )
    },
    [setSp],
  )

  /** Switch basin: reset well/depth/tab so nothing from the old basin lingers. */
  const setBasin = useCallback(
    (basin: Basin) => set({ basin: basin === 'assam' ? null : basin, well: null, md: null, tab: null }),
    [set],
  )

  return { ...state, set, setBasin }
}
