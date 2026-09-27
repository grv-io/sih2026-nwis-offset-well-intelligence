import { render, screen } from '@testing-library/react'
import { SeverityBadge } from './SeverityBadge'

describe('SeverityBadge', () => {
  it.each([
    ['critical', 'Critical', 'P1'],
    ['high', 'High', 'P2'],
    ['medium', 'Medium', 'P3'],
    ['low', 'Low', 'P4'],
  ])('renders %s with a text label and priority, not colour alone', (sev, label, prio) => {
    render(<SeverityBadge severity={sev} showPriority />)
    const badge = screen.getByTestId('severity-badge')
    expect(badge).toHaveAttribute('data-severity', sev)
    expect(badge).toHaveTextContent(label)
    expect(badge).toHaveTextContent(prio)
    expect(badge).toHaveAccessibleName(`${label} priority`)
    expect(badge.querySelector('svg')).not.toBeNull() // shape channel
  })

  it('normalises unknown / upper-case input instead of crashing', () => {
    render(<SeverityBadge severity="HIGH" />)
    expect(screen.getByTestId('severity-badge')).toHaveAttribute('data-severity', 'high')
  })

  it('falls back to medium for garbage', () => {
    render(<SeverityBadge severity="banana" />)
    expect(screen.getByTestId('severity-badge')).toHaveAttribute('data-severity', 'medium')
  })

  it('announces acknowledged state when muted', () => {
    render(<SeverityBadge severity="critical" muted />)
    expect(screen.getByTestId('severity-badge')).toHaveAccessibleName('Critical priority, acknowledged')
  })
})
