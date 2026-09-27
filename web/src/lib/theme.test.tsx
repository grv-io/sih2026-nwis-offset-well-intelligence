import { fireEvent, render, screen } from '@testing-library/react'
import tokensCss from '../styles/tokens.css?raw'
import { PALETTE, THEME_KEY, ThemeProvider, initialTheme, plotlyLayoutFor, type Theme } from './theme'
import { ThemeToggle } from '../components/Preferences'
import { themeFigure } from '../components/PlotlyFigure'

// ---- parse tokens.css into { dark: {bg: [r,g,b]...}, light: {...} }
function block(selector: string): Record<string, number[]> {
  const i = tokensCss.indexOf(selector + ' {')
  const body = tokensCss.slice(i, tokensCss.indexOf('}', i))
  const out: Record<string, number[]> = {}
  for (const m of body.matchAll(/--([\w-]+):\s*(\d+) (\d+) (\d+);/g)) out[m[1]] = [+m[2], +m[3], +m[4]]
  return out
}
const DARK = block(':root')
const LIGHT = { ...DARK, ...block(':root[data-theme="light"]') }
const TOKENS: Record<Theme, Record<string, number[]>> = { dark: DARK, light: LIGHT }

const lum = ([r, g, b]: number[]) => {
  const f = (c: number) => ((c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
const contrast = (a: number[], b: number[]) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}
const hex = (rgb: number[]) => '#' + rgb.map((v) => v.toString(16).padStart(2, '0')).join('')

function mockSystem(light: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: light && q.includes('light'),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia
}

describe('tokens.css (both themes)', () => {
  it.each(['dark', 'light'] as Theme[])('%s: body text >= 4.5:1 and UI accents >= 3:1 on panels', (theme) => {
    const tk = TOKENS[theme]
    for (const surface of ['s1', 's2']) {
      for (const text of ['ink', 'ink2', 'dim', 'crit', 'high', 'med', 'low', 'ok']) {
        expect(contrast(tk[text], tk[surface]), `${theme} ${text} on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
      expect(contrast(tk.accent, tk[surface]), `${theme} accent on ${surface}`).toBeGreaterThanOrEqual(3)
    }
    expect(contrast(tk['accent-ink'], tk.accent), `${theme} text on primary button`).toBeGreaterThanOrEqual(4.5)
  })

  it('light theme really is light and keeps P1-P4 apart', () => {
    expect(lum(LIGHT.s1)).toBeGreaterThan(0.9)
    expect(lum(LIGHT.ink)).toBeLessThan(0.02)
    expect(contrast(LIGHT.risk3, LIGHT.s1)).toBeGreaterThanOrEqual(3)
    const alarms = ['crit', 'high', 'med', 'low'].map((k) => LIGHT[k])
    for (let i = 0; i < alarms.length; i++)
      for (let j = i + 1; j < alarms.length; j++) {
        const d = Math.hypot(...alarms[i].map((v, n) => v - alarms[j][n]))
        expect(d, `alarm ${i} vs ${j}`).toBeGreaterThan(45) // clearly different colours, not shades of one
      }
  })

  it.each(['dark', 'light'] as Theme[])('%s: the TS palette mirrors tokens.css', (theme) => {
    const p = PALETTE[theme]
    const pairs: [keyof typeof p, string][] = [['bg', 'bg'], ['s1', 's1'], ['s2', 's2'], ['s3', 's3'], ['line', 'line'],
      ['rule', 'rule'], ['ink', 'ink'], ['ink2', 'ink2'], ['dim', 'dim'], ['faint', 'faint'], ['accent', 'accent'],
      ['crit', 'crit'], ['high', 'high'], ['med', 'med'], ['low', 'low'], ['ok', 'ok'], ['plotBg', 'plot-bg']]
    for (const [k, token] of pairs) expect(p[k], `${theme}.${k}`).toBe(hex(TOKENS[theme][token]))
  })
})

describe('theme choice', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
  })

  it('defaults to dark, respects prefers-color-scheme on first visit, stored choice wins', () => {
    mockSystem(false)
    expect(initialTheme()).toBe('dark')
    mockSystem(true)
    expect(initialTheme()).toBe('light')
    localStorage.setItem(THEME_KEY, 'dark')
    expect(initialTheme()).toBe('dark')
  })

  it('the toggle flips data-theme, persists nwis.theme and relabels itself', () => {
    mockSystem(false)
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    )
    const btn = screen.getByTestId('theme-toggle')
    expect(btn.tagName).toBe('BUTTON') // native button: Enter/Space work, focusable
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(btn).toHaveAccessibleName('Switch to light theme')
    expect(btn).toHaveAttribute('data-tip', 'Switch to light theme')
    fireEvent.click(btn)
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(localStorage.getItem(THEME_KEY)).toBe('light')
    expect(btn).toHaveAccessibleName('Switch to dark theme')
    fireEvent.click(btn)
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
  })
})

describe('plotlyLayoutFor', () => {
  it('re-themes paper, grid, hover and severity markers per theme', () => {
    const d = plotlyLayoutFor('dark')
    const l = plotlyLayoutFor('light')
    expect(d.layout.plot_bgcolor).toBe(PALETTE.dark.plotBg)
    expect(l.layout.plot_bgcolor).toBe(PALETTE.light.plotBg)
    expect(l.axis.gridcolor).toBe(PALETTE.light.rule)
    expect(l.severity.critical).toBe(PALETTE.light.crit)
    expect(d.severity.critical).not.toBe(l.severity.critical)
  })

  it('themeFigure applies it to a backend figure without touching data', () => {
    const fig = {
      data: [{ name: 'critical', x: ['A'], y: [1], marker: { symbol: ['circle'] } }, { name: 'anchor', x: ['A'], y: [null] }],
      layout: { yaxis: { title: { text: 'TVD (m)' } }, xaxis: {}, annotations: [{ text: 'A' }], template: {} },
    }
    const out = themeFigure(fig, 400, undefined, 'light')
    const tr = out.data[0] as { marker: { color: string; line: { color: string } }; x: string[]; name: string }
    expect(tr.marker.color).toBe(PALETTE.light.crit)
    expect(tr.marker.line.color).toBe(PALETTE.light.s1)
    expect(tr.x).toEqual(['A'])
    expect(tr.name).toBe('P1 Critical')
    expect(out.layout.plot_bgcolor).toBe(PALETTE.light.plotBg)
    expect((out.layout.yaxis as { gridcolor: string }).gridcolor).toBe(PALETTE.light.rule)
    expect(out.data[1]).toBe(fig.data[1])
  })
})
