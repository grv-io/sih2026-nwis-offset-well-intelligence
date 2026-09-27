// Tiny SVG sparkline: no chart library on the rig bundle path (VSAT). Colours
// are CSS custom properties applied via `style` (not presentation attributes),
// so the sparkline re-themes with tokens.css without a re-render.
export function Sparkline({ values, width = 200, height = 40, stroke = 'rgb(var(--ink2))', band }: {
  values: (number | null | undefined)[]
  width?: number
  height?: number
  stroke?: string
  band?: { mean: number; std: number } // shaded ±2σ of the rolling window
}) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => typeof p[1] === 'number')
  if (pts.length < 2) {
    return <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden />
  }
  let lo = Math.min(...pts.map((p) => p[1]))
  let hi = Math.max(...pts.map((p) => p[1]))
  if (band) {
    lo = Math.min(lo, band.mean - 2 * band.std)
    hi = Math.max(hi, band.mean + 2 * band.std)
  }
  if (hi - lo < 1e-9) {
    hi += 1
    lo -= 1
  }
  const n = Math.max(values.length - 1, 1)
  const pad = 3
  const x = (i: number) => (i / n) * width
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (height - 2 * pad)
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('')
  const last = pts[pts.length - 1]
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      {band && band.std > 0 && (
        <rect
          x={0}
          width={width}
          y={y(band.mean + 2 * band.std)}
          height={Math.max(0, y(band.mean - 2 * band.std) - y(band.mean + 2 * band.std))}
          style={{ fill: 'rgb(var(--ink2) / 0.08)' }}
        />
      )}
      <path d={d} style={{ fill: 'none', stroke }} strokeWidth={1.5} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      <circle cx={x(last[0])} cy={y(last[1])} r={2.5} style={{ fill: stroke }} />
    </svg>
  )
}
