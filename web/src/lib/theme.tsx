import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

// Theme = dark control-room (default) or light "paper". The CSS side lives in
// styles/tokens.css (:root / :root[data-theme="light"]); this module owns the
// choice, its persistence, and the TS mirror of the tokens for the two renderers
// that cannot read CSS variables: Plotly (own colour parser) and the Leaflet
// canvas renderer. theme.test.tsx fails if this mirror drifts from tokens.css.

export type Theme = 'dark' | 'light'
export const THEME_KEY = 'nwis.theme'

export function readStoredTheme(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return v === 'light' || v === 'dark' ? v : null
  } catch {
    return null
  }
}

export function systemTheme(): Theme {
  try {
    return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

/** Stored choice wins; else the OS scheme on first visit; dark otherwise. */
export function initialTheme(): Theme {
  return readStoredTheme() ?? systemTheme()
}

export function applyTheme(theme: Theme, animate = false) {
  const root = document.documentElement
  if (animate && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    root.classList.add('theme-anim')
    window.setTimeout(() => root.classList.remove('theme-anim'), 260)
  }
  root.setAttribute('data-theme', theme)
}

interface ThemeCtx {
  theme: Theme
  setTheme: (t: Theme) => void
  toggle: () => void
}

const Ctx = createContext<ThemeCtx>({ theme: 'dark', setTheme: () => {}, toggle: () => {} })

export function ThemeProvider({ children, initial }: { children: ReactNode; initial?: Theme }) {
  const [theme, set] = useState<Theme>(() => initial ?? initialTheme())

  // Keep the attribute in sync (index.html already set it before first paint).
  useEffect(() => applyTheme(theme), [theme])

  // Until the user picks explicitly, follow the OS if it flips (e.g. night mode).
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: light)')
    if (!mq?.addEventListener) return
    const on = (e: MediaQueryListEvent) => {
      if (!readStoredTheme()) set(e.matches ? 'light' : 'dark')
    }
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  const setTheme = useCallback((t: Theme) => {
    try {
      localStorage.setItem(THEME_KEY, t)
    } catch {
      /* private mode: the choice just won't persist */
    }
    // Flip the attribute before React re-renders so anything that reads
    // computed styles during render already sees the new theme.
    applyTheme(t, true)
    set(t)
  }, [])
  const toggle = useCallback(() => setTheme(theme === 'dark' ? 'light' : 'dark'), [theme, setTheme])
  const value = useMemo(() => ({ theme, setTheme, toggle }), [theme, setTheme, toggle])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useTheme = () => useContext(Ctx)

// --------------------------------------------------------------------------
// TS mirror of tokens.css (hex), for Plotly and the Leaflet canvas.
// --------------------------------------------------------------------------
export interface Palette {
  bg: string
  s1: string
  s2: string
  s3: string
  line: string
  rule: string
  ink: string
  ink2: string
  dim: string
  faint: string
  accent: string
  crit: string
  high: string
  med: string
  low: string
  ok: string
  plotBg: string
}

export const PALETTE: Record<Theme, Palette> = {
  dark: {
    bg: '#0a0d11', s1: '#10141a', s2: '#161b22', s3: '#1e242d', line: '#2b333e', rule: '#1e242c',
    ink: '#e6ebf1', ink2: '#bcc5d0', dim: '#8b96a3', faint: '#687380', accent: '#4fc3d7',
    crit: '#ef4444', high: '#f59e0b', med: '#eac83c', low: '#6092f0', ok: '#34b26a', plotBg: '#0c1015',
  },
  light: {
    bg: '#eef1f5', s1: '#ffffff', s2: '#f6f8fa', s3: '#e7ebf0', line: '#cdd4dd', rule: '#e2e7ed',
    ink: '#111720', ink2: '#333c48', dim: '#525c69', faint: '#68717e', accent: '#0b7285',
    crit: '#c81e1e', high: '#ac4e06', med: '#856500', low: '#2f5fd0', ok: '#1f7a4a', plotBg: '#fbfcfd',
  },
}

/** The basemap is the dark-filtered OSM layer in both themes (tiles unchanged by
 *  the light theme), so map-canvas colours are fixed and tuned for it. */
export const MAP_COLORS = {
  ring: '#4fc3d7',      // signal cyan on the dark basemap
  focus: '#4fc3d7',
  activeEdge: '#e6ebf1',
  candEdge: '#e6ebf1',
  plainEdge: '#0a0d11',
  noFamily: '#78828e',
} as const

export interface PlotlyTheme {
  /** Spread into the figure layout. */
  layout: Record<string, unknown>
  /** Merged into every x/y axis. */
  axis: { gridcolor: string; linecolor: string; zerolinecolor: string; tickfont: { color: string; size: number }; titleFont: { color: string; size: number } }
  annotationColor: string
  markerEdge: string
  severity: Record<'critical' | 'high' | 'medium' | 'low', string>
}

const PLOT_FONT = 'Segoe UI, Nirmala UI, Noto Sans Devanagari, system-ui, sans-serif'

/** Plotly styling for the current theme: paper transparent (the panel shows
 *  through), recessive grid, ISA severity colours re-tuned per theme. */
export function plotlyLayoutFor(theme: Theme): PlotlyTheme {
  const p = PALETTE[theme]
  return {
    layout: {
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: p.plotBg,
      font: { family: PLOT_FONT, color: p.ink2, size: 12 },
      legend: { font: { color: p.ink2, size: 11 }, bgcolor: 'rgba(0,0,0,0)' },
      hoverlabel: { bgcolor: p.s1, bordercolor: p.line, font: { color: p.ink, size: 12, family: PLOT_FONT } },
    },
    axis: {
      gridcolor: p.rule,
      linecolor: p.line,
      zerolinecolor: p.line,
      tickfont: { color: p.dim, size: 11 },
      titleFont: { color: p.dim, size: 11 },
    },
    annotationColor: p.ink2,
    markerEdge: theme === 'dark' ? p.bg : p.s1,
    severity: { critical: p.crit, high: p.high, medium: p.med, low: p.low },
  }
}
