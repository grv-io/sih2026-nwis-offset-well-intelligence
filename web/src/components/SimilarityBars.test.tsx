import { render, screen } from '@testing-library/react'
import { SimilarityBars } from './SimilarityBars'

const dims = { geographic: 0.8, formation_overlap: 1, trajectory_type: 0, target_depth: 0.5 }

describe('SimilarityBars', () => {
  it('renders one slot per dimension in fixed order', () => {
    const { container } = render(<SimilarityBars dimensions={{ ...dims, difficulty: 0.3 }} />)
    const keys = [...container.querySelectorAll('[data-dim]')].map((n) => n.getAttribute('data-dim'))
    expect(keys).toEqual(['geographic', 'formation_overlap', 'trajectory_type', 'target_depth', 'difficulty'])
  })

  it('shows unavailable dimensions as muted n/a, never as a zero bar', () => {
    const { container } = render(<SimilarityBars dimensions={dims} unavailable={['difficulty']} />)
    const diff = container.querySelector('[data-dim="difficulty"]')!
    expect(diff).toHaveAttribute('data-na', 'true')
    expect(diff).toHaveTextContent('n/a')
    expect(diff.querySelector('[data-testid="bar-fill"]')).toBeNull()
    expect(screen.getAllByTestId('bar-fill')).toHaveLength(4)
  })

  it('bar height follows the value and a real zero stays empty', () => {
    const { container } = render(<SimilarityBars dimensions={{ ...dims, difficulty: 0.3 }} />)
    const h = (k: string) => (container.querySelector(`[data-dim="${k}"] [data-testid="bar-fill"]`) as HTMLElement).style.height
    expect(h('formation_overlap')).toBe('100%')
    expect(h('geographic')).toBe('80%')
    expect(h('trajectory_type')).toBe('0%')
  })

  it('titles carry value and renormalised weight', () => {
    const { container } = render(
      <SimilarityBars dimensions={dims} weights={{ geographic: 0.3889 }} unavailable={['difficulty']} />,
    )
    expect(container.querySelector('[data-dim="geographic"]')!.getAttribute('title')).toMatch(/80%.*weight 39%/)
  })
})
