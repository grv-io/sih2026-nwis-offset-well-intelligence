import { useMemo } from 'react'
import Plotly from 'plotly.js-basic-dist-min'
import type { Data, Layout } from 'plotly.js'
import createPlotlyComponent from 'react-plotly.js/factory'
import { SEVERITY_META } from '../lib/hazards'
import { plotlyLayoutFor, useTheme, type Theme } from '../lib/theme'
import { useT, type TFn } from '../i18n'
import type { Severity } from '../lib/types'

const Plot = createPlotlyComponent(Plotly)

const SEV_NAMES = new Set(['low', 'medium', 'high', 'critical'])

type Obj = Record<string, unknown>

/** Re-skin a backend Plotly figure (nwis/geo/panel.py) for the current theme
 *  without changing its data: transparent paper, recessive grid, ISA severity
 *  colours on the event markers (the server's palette predates the UI), and
 *  localised legend / axis labels. */
export function themeFigure(
  fig: { data: Obj[]; layout: Obj },
  height: number,
  yCategories: string[] | undefined,
  theme: Theme = 'dark',
  t?: TFn,
) {
  const pt = plotlyLayoutFor(theme)
  // panel.py only shows legend entries from column 1; show each severity once, wherever it first appears
  const shown = new Set<string>()
  const data = fig.data.map((tr) => {
    const name = String(tr.name ?? '')
    if (!SEV_NAMES.has(name)) return tr
    const sev = name as Severity
    const showlegend = !shown.has(sev)
    shown.add(sev)
    const meta = SEVERITY_META[sev]
    const marker = { ...(tr.marker as Obj), color: pt.severity[sev], line: { width: 1.5, color: pt.markerEdge }, size: 11 }
    const label = t ? t(`sev.${sev}`) : meta.label
    return { ...tr, marker, showlegend, legendrank: 4 - meta.rank, name: `${meta.priority} ${label}` }
  })
  const layout: Obj = { ...fig.layout }
  delete layout.template
  delete layout.title
  const axisTitle = (text: string) => ({ text, font: pt.axis.titleFont })
  for (const [k, v] of Object.entries(layout)) {
    if (/^[xy]axis\d*$/.test(k)) {
      const ax = v as Obj
      layout[k] = {
        ...ax,
        gridcolor: pt.axis.gridcolor,
        showgrid: k.startsWith('y'),
        zeroline: false,
        zerolinecolor: pt.axis.zerolinecolor,
        linecolor: pt.axis.linecolor,
        tickfont: pt.axis.tickfont,
        title: ax.title ? { ...(ax.title as Obj), font: pt.axis.titleFont } : undefined,
      }
      if (k === 'yaxis' && !yCategories && ax.title) {
        layout[k] = { ...(layout[k] as Obj), title: axisTitle(t ? t('corr.axisTvd') : 'TVD (m)') }
      }
      if (yCategories && k.startsWith('yaxis')) {
        // formation-aligned mode: y = formation index + fraction; label the bands by name
        layout[k] = {
          ...(layout[k] as Obj),
          tickvals: yCategories.map((_, i) => i + 0.5),
          ticktext: yCategories,
          title: k === 'yaxis' ? axisTitle(t ? t('corr.axisAligned') : 'Formation (aligned on top)') : undefined,
        }
      }
    }
  }
  const annotations = ((layout.annotations as Obj[]) ?? []).map((a) => ({
    ...a,
    font: { color: pt.annotationColor, size: ((a.font as Obj | undefined)?.size as number) ?? 12, family: 'Cascadia Mono, Consolas, monospace' },
  }))
  const shapes = ((layout.shapes as Obj[]) ?? []).map((s) => ({ ...s, opacity: theme === 'light' ? 0.22 : 0.3 }))
  return {
    data,
    layout: {
      ...layout,
      ...pt.layout,
      annotations,
      shapes,
      height,
      autosize: true,
      margin: { l: 64, r: 16, t: 36, b: 16 },
      legend: { ...(pt.layout.legend as Obj), orientation: 'h', x: 0, y: -0.02, yanchor: 'top' },
    } as Obj,
  }
}

export default function PlotlyFigure({ figure, height, yCategories }: { figure: { data: Obj[]; layout: Obj }; height: number; yCategories?: string[] }) {
  const { theme } = useTheme()
  const { t } = useT()
  const themed = useMemo(() => themeFigure(figure, height, yCategories, theme, t), [figure, height, yCategories, theme, t])
  return (
    <Plot
      data={themed.data as Data[]}
      layout={themed.layout as Partial<Layout>}
      config={{ displaylogo: false, responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d', 'autoScale2d'] }}
      useResizeHandler
      style={{ width: '100%', height }}
    />
  )
}
